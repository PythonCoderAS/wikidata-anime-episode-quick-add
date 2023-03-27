import re
from dataclasses import dataclass
from typing import Optional

from pywikibot import (
    Claim,
    ItemPage,
    Timestamp,
    WbMonolingualText,
    WbQuantity,
    WbTime,
    sleep,
)
from pywikibot.pagegenerators import WikidataSPARQLPageGenerator
from requests import ConnectionError, HTTPError
from wikidata_bot_framework import (
    EntityPage,
    ExtraProperty,
    ExtraQualifier,
    ExtraReference,
    Output,
    OutputHelper,
    PropertyAdderBot,
    session,
    site,
    start_span,
    start_transaction,
    url_prop,
)
from wikidata_fast_query import ItemContainer

part_of_series = "P179"
series_ordinal = "P1545"
instance_of = "P31"
anime_tv_series_episode = "Q102364578"
origin_country = "P495"
japan = "Q17"
original_language = "P364"
japanese = "Q5287"
publication_date = "P577"
follows = "P155"
followed_by = "P156"
title = "P1476"
season = "P4908"
number_of_episodes = "P1113"
has_parts = "P527"
mal_id = "P4086"
myanimelist = "Q4044680"
stated_in = "P248"


@dataclass
class EpisodeData:
    number: int
    title_en: str
    title_ja: Optional[str]
    title_romaji: Optional[str]
    aired: Optional[Timestamp]


class Bot(PropertyAdderBot):
    def __init__(
        self,
        episode_data: list[EpisodeData],
        season_item: ItemPage,
        myanimelist_id: str,
        old_edit_group_id: Optional[str] = None,
    ):
        super().__init__()
        self.episode_data = episode_data
        self.season_item = season_item
        self.myanimelist_id = myanimelist_id
        anime_claim = ItemContainer(season_item).claims(part_of_series).first()
        if anime_claim is None:
            self.anime_item = ItemPage(site, input("Anime item ID: "))
        else:
            self.anime_item = anime_claim.value
        self.season_container = ItemContainer(self.season_item)
        self.anime_container = ItemContainer(self.anime_item)
        self.episode_items: list[ItemPage] = self.season_container.claims(
            has_parts
        ).values or list(WikidataSPARQLPageGenerator("SELECT ?item WHERE {?item wdt:%s wd:%s . }" % (season, self.season_item.id), site=site))
        if len(self.episode_items) != 0:
            assert len(self.episode_items) <= len(
                self.episode_data
            ), "Episode count mismatch"
        if num_eps_claim := self.season_container.claims(number_of_episodes).first():
            assert num_eps_claim.value.amount == len(
                self.episode_data
            ), "Episode count mismatch"
        self.old_edit_group_id = old_edit_group_id

    def get_edit_group_id(self) -> str:
        if self.old_edit_group_id:
            return self.old_edit_group_id
        return super().get_edit_group_id()

    def get_edit_summary(self, page: ItemPage) -> str:
        if page.id == "-1":
            return "Creating episode item."
        elif page == self.season_item:
            return "Adding episodes to season."
        elif (
            ItemContainer(page).claims(instance_of).first().value.id
            == anime_tv_series_episode
        ):
            return "Adding episode sequential data."
        return "Adding data."

    def run_item(self, _):
        return {}

    def reference(self) -> ExtraReference:
        ref = ExtraReference(
            url_match_pattern=re.compile(
                rf"^https://myanimelist.net/anime/{self.myanimelist_id}"
            )
        )
        claim = Claim(site, stated_in)
        claim.setTarget(ItemPage(site, myanimelist))
        ref.add_claim(claim, also_match_property_values=True)
        claim = Claim(site, mal_id)
        claim.setTarget(self.myanimelist_id)
        ref.add_claim(claim, also_match_property_values=True)
        claim = Claim(site, url_prop)
        claim.setTarget(
            f"https://myanimelist.net/anime/{self.myanimelist_id}/_/episode"
        )
        ref.add_claim(claim, also_match_property_values=False)
        return ref

    def process(self, output: Output, item: EntityPage) -> bool:
        for props in output.values():
            for prop in props:
                prop.add_reference(self.reference())
        return super().process(output, item)

    def run(self):
        if not self.episode_items or len(self.episode_items) < len(self.episode_data):
            for episode in self.episode_data[len(self.episode_items):]:
                with start_transaction(
                    op="create_episode_item", name="Creating episode item"
                ):
                    new_item = self.make_episode_item_output(episode)
                    new_item.get(force=True)
                    self.episode_items.append(new_item)
        season_oh = OutputHelper()
        prop = ExtraProperty.from_property_id_and_value(
            number_of_episodes, WbQuantity(len(self.episode_data), site=site)
        )
        prop.skip_if_conflicting_exists = True
        season_oh.add_property(prop)
        for num, (episode_data, item) in enumerate(
            zip(self.episode_data, self.episode_items), start=1
        ):
            prop = ExtraProperty.from_property_id_and_value(has_parts, item)
            prop.add_qualifier_with_property_id_and_value(series_ordinal, str(num))
            with start_transaction(
                op="add_episode_links", name="Linking episodes to each other"
            ):
                self.link_episode_item(episode_data, item)
            season_oh.add_property(prop)
        with start_transaction(
            op="add_season_links", name="Linking season to episodes"
        ):
            self.process(season_oh, self.season_item)

    def link_episode_item(self, episode: EpisodeData, item: ItemPage):
        oh = OutputHelper()
        extra_property = ExtraProperty.from_property_id_and_value(
            season, self.season_item
        )
        oh.add_property(extra_property)
        if episode.number == 1:
            claim = Claim(site, follows)
            claim.setSnakType("novalue")
            oh.add_property(ExtraProperty(claim))
            claim = Claim(site, follows)
            claim.setSnakType("novalue")
            extra_property.add_qualifier(ExtraQualifier(claim))
        else:
            oh.add_property_from_property_id_and_value(
                follows, self.episode_items[episode.number - 2]
            )
            extra_property.add_qualifier_with_property_id_and_value(
                follows, self.episode_items[episode.number - 2]
            )
        if episode.number == len(self.episode_data):
            claim = Claim(site, followed_by)
            claim.setSnakType("novalue")
            oh.add_property(ExtraProperty(claim))
            claim = Claim(site, followed_by)
            claim.setSnakType("novalue")
            extra_property.add_qualifier(ExtraQualifier(claim))
        else:
            oh.add_property_from_property_id_and_value(
                followed_by, self.episode_items[episode.number]
            )
            extra_property.add_qualifier_with_property_id_and_value(
                followed_by, self.episode_items[episode.number]
            )
        self.process(oh, item)

    def make_episode_item_output(self, episode: EpisodeData) -> ItemPage:
        episode_num = episode.number
        item = ItemPage(site)
        item.labels["en"] = episode.title_en
        item.aliases["en"] = []
        if (
            episode.title_romaji
            and episode.title_romaji.strip() != episode.title_en.strip()
        ):
            item.aliases["en"].append(episode.title_romaji)
        anime_name_en = self.anime_container.labels("en")
        if anime_name_en is not None:
            item.aliases["en"].append(f"{anime_name_en} Episode {episode_num}")
            item.aliases["en"].append(f"{anime_name_en} ep {episode_num}")
            item.aliases["en"].append(f"{anime_name_en} ep. {episode_num}")
            item.aliases["en"].append(f"{anime_name_en} ep{episode_num}")
        season_claim = self.season_container.claims(part_of_series).first()
        season_number_claim = (
            season_claim.qualifiers(series_ordinal).first()
            if season_claim is not None
            else None
        )
        if season_number_claim is not None:
            season_number = season_number_claim.value
            item.aliases["en"].append(
                f"{anime_name_en} Season {season_number} Episode {episode_num}"
            )
            item.aliases["en"].append(
                f"{anime_name_en} Season {season_number} ep {episode_num}"
            )
            item.aliases["en"].append(
                f"{anime_name_en} Season {season_number} ep. {episode_num}"
            )
            item.aliases["en"].append(
                f"{anime_name_en} S {season_number} ep {episode_num}"
            )
            item.aliases["en"].append(
                f"{anime_name_en} S {season_number} ep. {episode_num}"
            )
            item.aliases["en"].append(
                f"{anime_name_en} S{season_number}EP{episode_num}"
            )
            item.aliases["en"].append(
                f"{anime_name_en} S{season_number:0>2}EP{episode_num:0>2}"
            )
            item.aliases["en"].append(f"{anime_name_en} S{season_number}E{episode_num}")
            item.aliases["en"].append(
                f"{anime_name_en} S{season_number:0>2}E{episode_num:0>2}"
            )
            if anime_name_en:
                item.descriptions[
                    "en"
                ] = f"Episode {episode_num} of {anime_name_en} Season {season_number}"
        else:
            if anime_name_en:
                item.descriptions["en"] = f"Episode {episode_num} of {anime_name_en}"
        oh = OutputHelper()
        oh.add_property_from_property_ids_and_item_id_values(
            {
                instance_of: anime_tv_series_episode,
                origin_country: japan,
                original_language: japanese,
            }
        )
        if episode.aired:
            oh.add_property_from_property_id_and_value(publication_date, WbTime.fromTimestamp(
                    episode.aired, precision=WbTime.PRECISION["day"]
                ))
        oh.add_property_from_property_id_and_value(part_of_series, self.anime_item)
        if episode.title_ja:
            oh.add_property_from_property_id_and_value(
                title, WbMonolingualText(episode.title_ja, "ja")
            )
            item.labels["ja"] = episode.title_ja
        extra_property = ExtraProperty.from_property_id_and_value(
            season, self.season_item
        )
        extra_property.add_qualifier_with_property_id_and_value(
            series_ordinal, str(episode_num)
        )
        oh.add_property(extra_property)
        self.process(oh, item)
        return item


def main():
    season_ids = [
        season_id.strip()
        for season_id in input("Enter season item ID (for multiple separate with |): ")
        .strip()
        .split("|")
    ]
    old_eg_id = None
    for season_id in season_ids:
        season_item = ItemPage(site, season_id)
        container = ItemContainer(season_item)
        myanimelist_claim = container.claims(mal_id).first()
        if myanimelist_claim is None:
            myanimelist_id = input("Enter MyAnimeList ID: ").strip()
        else:
            myanimelist_id = myanimelist_claim.value
        episode_data: list[EpisodeData] = []
        count = None
        page = 1
        while True:
            try:
                r = session.get(
                    f"https://api.jikan.moe/v4/anime/{myanimelist_id}/episodes?page={page}"
                )
                r.raise_for_status()
                data = r.json()
                assert "data" in data
                if count is None:
                    count = data["pagination"]["last_visible_page"]
                episode_data.extend(
                    [
                        EpisodeData(
                            number=num,
                            title_en=item["title"],
                            title_ja=item["title_japanese"],
                            title_romaji=item["title_romanji"],
                            aired=Timestamp.fromisoformat(item["aired"]) if item["aired"] else None,
                        )
                        for num, item in enumerate(data["data"], 1)
                    ]
                )
                if page == count:
                    break
                else:
                    page += 1
            except (ConnectionError, HTTPError, AssertionError):
                sleep(5)
        bot = Bot(
            episode_data, season_item, myanimelist_id, old_edit_group_id=old_eg_id
        )
        bot.run()
        old_eg_id = bot.get_edit_group_id()


if __name__ == "__main__":
    main()

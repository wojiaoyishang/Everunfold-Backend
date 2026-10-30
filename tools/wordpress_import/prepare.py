"""Extract an allowlisted public dataset; original SQL and accounts stay outside Git."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from tools.wordpress_import.files import write_text_atomic
from tools.wordpress_import.links import PublicLinkResolver
from tools.wordpress_import.media import MediaResolver
from tools.wordpress_import.mysql_dump import iter_table_rows, php_array
from tools.wordpress_import.text import (
    plain_text,
    rewrite_html,
    rewrite_markdown,
    text_outside_code,
)

OPTION_KEYS = {
    "blogname",
    "blogdescription",
    "home",
    "siteurl",
    "permalink_structure",
    "timezone_string",
    "sticky_posts",
    "theme_mods_argon",
    "argon_theme_color",
    "argon_toolbar_title",
    "argon_banner_title",
    "argon_banner_subtitle",
    "argon_banner_background_url",
    "argon_page_background_url",
    "argon_page_background_dark_url",
    "argon_page_background_opacity",
    "argon_page_background_banner_style",
    "argon_sidebar_auther_name",
    "argon_sidebar_auther_image",
    "argon_sidebar_author_description",
    "argon_banner_size",
    "argon_page_layout",
    "argon_card_radius",
    "argon_base_font_size",
    "argon_font",
    "woocommerce_shop_page_id",
    "woocommerce_cart_page_id",
    "woocommerce_checkout_page_id",
    "woocommerce_myaccount_page_id",
    "woocommerce_terms_page_id",
}
META_KEYS = {
    "_thumbnail_id",
    "_wp_attached_file",
    "_wp_page_template",
    "_menu_item_type",
    "_menu_item_object_id",
    "_menu_item_object",
    "_menu_item_url",
    "_menu_item_menu_item_parent",
}


def stable_id(origin: str, kind: str, source_id: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{origin.rstrip('/')}/wordpress/{kind}/{source_id}"))


def iso_date(gmt: Any, local: Any, timezone: ZoneInfo) -> str:
    raw = str(gmt or "")
    if raw and not raw.startswith("0000-"):
        return datetime.fromisoformat(raw).replace(tzinfo=UTC).isoformat()
    raw = str(local or "")
    if not raw or raw.startswith("0000-"):
        raise ValueError("Published content has no valid timestamp")
    return datetime.fromisoformat(raw).replace(tzinfo=timezone).astimezone(UTC).isoformat()


def _shortcode_projection(value: str, issues: Counter[str]) -> str:
    # The renderer has no legacy executable shortcode adapter. Keep source
    # literal and never alter code examples while reporting missing adapters.
    names = set(re.findall(r"\[(?!/)([A-Za-z_][\w-]*)(?=[\s\]])", text_outside_code(value)))
    for name in names:
        issues["shortcode:" + name] += 1
    # Preserve source text for ordinary shortcodes: the safe renderer displays it
    # literally until a trusted component adapter has been implemented.
    return value


def extract_public(
    source: Path,
    backup: Path,
    *,
    prefix: str = "pikachu_",
    media_root: Path | None = None,
    public_prefix: str = "/uploads/wordpress",
    dry_run: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tables = {
        prefix + name
        for name in (
            "options",
            "posts",
            "postmeta",
            "terms",
            "term_taxonomy",
            "term_relationships",
            "comments",
        )
    }
    options: dict[str, str] = {}
    posts: dict[int, dict[str, Any]] = {}
    metadata: dict[int, dict[str, Any]] = defaultdict(dict)
    terms: dict[int, dict[str, Any]] = {}
    taxonomies: dict[int, dict[str, Any]] = {}
    relationships: list[tuple[int, int]] = []
    comments: list[dict[str, Any]] = []
    issues: Counter[str] = Counter()
    for table, row in iter_table_rows(source, tables):
        kind = table.removeprefix(prefix)
        if kind == "options" and row["option_name"] in OPTION_KEYS:
            options[str(row["option_name"])] = str(row["option_value"] or "")
        elif kind == "posts":
            if row["post_status"] == "publish" and row["post_type"] in {
                "post",
                "page",
                "nav_menu_item",
            }:
                if not row["post_password"]:
                    posts[int(row["ID"])] = {
                        key: row[key]
                        for key in (
                            "ID",
                            "post_type",
                            "post_title",
                            "post_content",
                            "post_excerpt",
                            "post_name",
                            "post_parent",
                            "post_date",
                            "post_date_gmt",
                            "post_modified",
                            "post_modified_gmt",
                            "menu_order",
                        )
                    }
                else:
                    issues["password_protected_excluded"] += 1
        elif kind == "postmeta" and row["meta_key"] in META_KEYS:
            metadata[int(row["post_id"])][str(row["meta_key"])] = row["meta_value"]
        elif kind == "terms":
            terms[int(row["term_id"])] = {"name": row["name"], "slug": row["slug"]}
        elif kind == "term_taxonomy" and row["taxonomy"] in {"category", "post_tag", "nav_menu"}:
            taxonomies[int(row["term_taxonomy_id"])] = {
                "term_id": int(row["term_id"]),
                "kind": row["taxonomy"],
            }
        elif kind == "term_relationships":
            relationships.append((int(row["object_id"]), int(row["term_taxonomy_id"])))
        elif (
            kind == "comments"
            and str(row["comment_approved"]) == "1"
            and row["comment_type"] in {"", "comment"}
        ):
            comments.append(
                {
                    key: row[key]
                    for key in (
                        "comment_ID",
                        "comment_post_ID",
                        "comment_author",
                        "comment_content",
                        "comment_date",
                        "comment_date_gmt",
                        "comment_parent",
                    )
                }
            )
    origin = options.get("home") or options.get("siteurl") or "https://lovepikachu.top"
    timezone = ZoneInfo(options.get("timezone_string") or "Asia/Shanghai")
    commerce_ids = {
        int(options[key])
        for key in OPTION_KEYS
        if key.startswith("woocommerce_") and options.get(key, "").isdigit()
    }
    public_posts = {
        key: row
        for key, row in posts.items()
        if row["post_type"] in {"post", "page"} and key not in commerce_ids
    }
    issues["commerce_system_pages_excluded"] = len(commerce_ids & posts.keys())
    resolver = MediaResolver(backup, origin, media_root, public_prefix, dry_run)
    author = html.unescape(options.get("argon_sidebar_auther_name") or "Yishang")
    sticky_raw = options.get("sticky_posts") or "a:0:{}"
    sticky = set(php_array(sticky_raw).values())
    ids = {source_id: stable_id(origin, "content", source_id) for source_id in public_posts}
    known_paths: dict[int, str] = {}

    def content_path(source_id: int, seen: set[int] | None = None) -> str:
        if source_id in known_paths:
            return known_paths[source_id]
        row = public_posts[source_id]
        slug = unquote(str(row["post_name"])) or str(source_id)
        if row["post_type"] == "post":
            date = datetime.fromisoformat(str(row["post_date"]))
            path = options.get("permalink_structure") or "/%postname%.html"
            tokens = {
                "%postname%": slug,
                "%post_id%": str(source_id),
                "%year%": f"{date.year:04d}",
                "%monthnum%": f"{date.month:02d}",
                "%day%": f"{date.day:02d}",
            }
            for key, value in tokens.items():
                path = path.replace(key, value)
            if "%" in path:
                issues["unsupported_permalink_token"] += 1
                path = "/" + slug + ".html"
        else:
            visited = set(seen or ())
            if source_id in visited:
                raise ValueError("Cyclic WordPress page parent relationship")
            visited.add(source_id)
            parent = int(row["post_parent"])
            path = (
                (content_path(parent, visited).rstrip("/") if parent in public_posts else "")
                + "/"
                + slug
                + "/"
            )
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("Unsafe generated public path")
        known_paths[source_id] = path
        return path

    for source_id in public_posts:
        content_path(source_id)
    link_resolver = PublicLinkResolver(
        origin, {"/", "/archives", "/categories", "/tags", "/search", *known_paths.values()}
    )
    category_links: dict[int, list[int]] = defaultdict(list)
    public_taxonomies: dict[int, dict[str, Any]] = {}
    for post_id, taxonomy_id in relationships:
        taxonomy = taxonomies.get(taxonomy_id)
        if post_id not in public_posts or not taxonomy or taxonomy["kind"] == "nav_menu":
            continue
        term_id = taxonomy["term_id"]
        term = terms[term_id]
        if taxonomy_id not in public_taxonomies:
            public_taxonomies[taxonomy_id] = {
                "id": stable_id(origin, "taxonomy", taxonomy_id),
                "legacy_id": taxonomy_id,
                "slug": unquote(str(term["slug"])),
                "name": html.unescape(str(term["name"])),
                "kind": "category" if taxonomy["kind"] == "category" else "tag",
                "count": 0,
            }
        public_taxonomies[taxonomy_id]["count"] += 1
        category_links[post_id].append(taxonomy_id)
    for taxonomy in public_taxonomies.values():
        path = f"/{taxonomy['kind']}/{taxonomy['slug']}"
        link_resolver.paths[path] = path
    public_comments: dict[str, list[dict[str, Any]]] = {
        content_id: [] for content_id in ids.values()
    }
    approved_posts = {
        int(row["comment_ID"]): int(row["comment_post_ID"])
        for row in comments
        if int(row["comment_post_ID"]) in ids
    }
    comment_authors = {
        int(row["comment_ID"]): plain_text(str(row["comment_author"]))
        for row in comments
        if int(row["comment_post_ID"]) in ids
    }
    for row in comments:
        post_id = int(row["comment_post_ID"])
        if post_id not in ids:
            continue
        parent = int(row["comment_parent"])
        parent_valid = approved_posts.get(parent) == post_id
        public_comments[ids[post_id]].append(
            {
                "id": stable_id(origin, "comment", int(row["comment_ID"])),
                "legacy_id": int(row["comment_ID"]),
                "parent_id": stable_id(origin, "comment", parent) if parent_valid else None,
                "author": plain_text(str(row["comment_author"]))[:200] or "访客",
                "content": plain_text(str(row["comment_content"])),
                "created_at": iso_date(row["comment_date_gmt"], row["comment_date"], timezone),
                "reply_to": comment_authors.get(parent) if parent_valid else None,
            }
        )
    for rows in public_comments.values():
        rows.sort(key=lambda item: (item["created_at"], item["id"]))
    contents: list[dict[str, Any]] = []
    for source_id, row in public_posts.items():
        body = _shortcode_projection(str(row["post_content"]), issues)
        format_kind = (
            "html"
            if re.search(r"<(?:p|div|h[1-6]|ul|ol|table|img|blockquote|figure|pre)\b", body, re.I)
            else "markdown"
        )
        resolver.references = {}
        rewritten = (
            rewrite_html(body, resolver.resolve, link_resolver.resolve)
            if format_kind == "html"
            else rewrite_markdown(body, resolver.resolve, link_resolver.resolve)
        )
        media_urls = dict(resolver.references)
        cover_url = None
        thumbnail_id = metadata[source_id].get("_thumbnail_id")
        attached = (
            metadata.get(int(thumbnail_id), {}).get("_wp_attached_file")
            if thumbnail_id and str(thumbnail_id).isdigit()
            else None
        )
        if attached:
            reference = resolver.resolve(
                origin.rstrip("/") + "/wp-content/uploads/" + str(attached)
            )
            if reference.startswith("site://media/"):
                cover_url = resolver.references[reference.removeprefix("site://media/")]["original"]
        text = plain_text(body)
        word_count = len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", text))
        excerpt = plain_text(str(row["post_excerpt"])) or text[:180].replace("\n", " ")
        linked = [public_taxonomies[key] for key in category_links[source_id]]
        contents.append(
            {
                "id": ids[source_id],
                "legacy_id": source_id,
                "kind": "article" if row["post_type"] == "post" else "page",
                "title": html.unescape(str(row["post_title"])),
                "path": content_path(source_id),
                "excerpt": excerpt,
                "language": "zh-CN",
                "author": author,
                "published_at": iso_date(row["post_date_gmt"], row["post_date"], timezone),
                "updated_at": iso_date(row["post_modified_gmt"], row["post_modified"], timezone),
                "cover_url": cover_url,
                "reading_minutes": max(1, math.ceil(word_count / 400)),
                "word_count": word_count,
                "pinned": source_id in sticky,
                "categories": [item for item in linked if item["kind"] == "category"],
                "tags": [item for item in linked if item["kind"] == "tag"],
                "comment_count": len(public_comments[ids[source_id]]),
                "content": [
                    {
                        "id": stable_id(origin, "block", source_id),
                        "type": format_kind,
                        "content": rewritten,
                    }
                ],
                "media_urls": media_urls,
            }
        )
    paths: list[str] = [item["path"] for item in contents]
    if len(paths) != len(set(paths)):
        raise ValueError("Generated public paths conflict; inspect before importing")
    navigation = [{"label": "首页", "href": "/", "icon": "home"}]
    theme_mods = php_array(options["theme_mods_argon"]) if options.get("theme_mods_argon") else {}
    menu_locations = theme_mods.get("nav_menu_locations", {})
    toolbar_menu = menu_locations.get("toolbar_menu")
    selected_menu_items = {
        post_id
        for post_id, taxonomy_id in relationships
        if taxonomies.get(taxonomy_id, {}).get("kind") == "nav_menu"
        and taxonomies[taxonomy_id]["term_id"] == toolbar_menu
    }
    for source_id, row in sorted(posts.items(), key=lambda entry: int(entry[1]["menu_order"])):
        if (
            row["post_type"] != "nav_menu_item"
            or toolbar_menu is not None
            and source_id not in selected_menu_items
            or metadata[source_id].get("_menu_item_menu_item_parent", "0") != "0"
        ):
            continue
        object_id = metadata[source_id].get("_menu_item_object_id", "")
        href = metadata[source_id].get("_menu_item_url", "")
        if str(object_id).isdigit() and int(object_id) in public_posts:
            href = content_path(int(object_id))
        if not href or (urlsplit(str(href)).scheme not in {"", "http", "https"}):
            continue
        parsed = urlsplit(str(href))
        if parsed.hostname == urlsplit(origin).hostname or str(href).startswith("/"):
            source_path = unquote(parsed.path) or "/"
            if source_path.rstrip("/") == "/article":
                href = "/archives"
            elif (source_path.rstrip("/") or "/") in link_resolver.paths:
                href = link_resolver.resolve(str(href))
            else:
                issues["unavailable_navigation_omitted"] += 1
                continue
        label = plain_text(str(row["post_title"]))
        if not label and str(object_id).isdigit() and int(object_id) in public_posts:
            label = str(public_posts[int(object_id)]["post_title"])
        if label and href not in {item["href"] for item in navigation}:
            navigation.append({"label": html.unescape(label), "href": href})
    site: dict[str, Any] = {
        "name": html.unescape(
            options.get("argon_toolbar_title") or options.get("blogname") or "Everunfold"
        ),
        "description": html.unescape(options.get("blogdescription", "")),
        "author": author,
        "avatar_url": "/reference/yishang-avatar.jpg",
        "hero_url": "/reference/campfire-background.webp",
        "hero_title": html.unescape(
            options.get("argon_banner_title") or options.get("blogname") or "Everunfold"
        ),
        "hero_subtitle": html.unescape(options.get("argon_banner_subtitle", "")),
        "notice": "",
        "navigation": navigation,
        "stats": {
            "articles": sum(item["kind"] == "article" for item in contents),
            "pages": sum(item["kind"] == "page" for item in contents),
            "comments": sum(map(len, public_comments.values())),
            "tags": sum(item["kind"] == "tag" for item in public_taxonomies.values()),
            "categories": sum(item["kind"] == "category" for item in public_taxonomies.values()),
        },
    }
    payload = {
        "format": "everunfold-wordpress-public-1",
        "site": site,
        "contents": contents,
        "taxonomies": list(public_taxonomies.values()),
        "comments": public_comments,
    }
    report = {
        "counts": {
            **site["stats"],
            "media": len(resolver.files),
            "media_bytes": sum(item["bytes"] for item in resolver.files.values()),
        },
        "issues": dict(sorted((issues + resolver.issues).items())),
        "media_issues": [
            {"reason": reason, "path": path} for reason, path in sorted(resolver.media_issues)
        ],
        "unmapped_same_origin_links": sorted(link_resolver.unmapped),
        "public_paths": sorted(paths),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "theme": {
            key: options[key]
            for key in options
            if key
            in {
                "argon_theme_color",
                "argon_page_background_banner_style",
                "argon_card_radius",
                "argon_base_font_size",
                "argon_page_layout",
                "argon_banner_size",
            }
        },
    }
    return payload, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Original SQL dump (read only)")
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--prefix", default="pikachu_")
    parser.add_argument("--output", type=Path, default=Path("var/wordpress-public.json"))
    parser.add_argument("--report", type=Path, default=Path("var/wordpress-public-report.json"))
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--public-prefix", default="/uploads/wordpress")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload, report = extract_public(
        args.source,
        args.backup_root,
        prefix=args.prefix,
        media_root=args.media_root,
        public_prefix=args.public_prefix,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        for path, value in ((args.output, payload), (args.report, report)):
            write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "counts": report["counts"],
                "issues": report["issues"],
                "media_issue_count": len(report["media_issues"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

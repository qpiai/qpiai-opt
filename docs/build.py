"""Build the public manual and check local links without importing the SDK."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import markdown
from bs4 import BeautifulSoup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUTPUT = DOCS / "_build_html"


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pages = json.loads((DOCS / "pages.json").read_text(encoding="utf-8"))
    sources = {page["sources"][0]: page["slug"] for page in pages}
    markdown_sources = {p.relative_to(ROOT).as_posix() for p in DOCS.glob("*.md")}
    if not markdown_sources <= sources.keys():
        raise ValueError(
            f"Pages missing from pages.json: {sorted(markdown_sources - sources.keys())}"
        )
    if len(sources) != len(pages) or len({p["slug"] for p in pages}) != len(pages):
        raise ValueError("Page sources and slugs must be unique")
    for page in pages:
        if len(page["sources"]) != 1 or not re.fullmatch(r"[a-z0-9-]+", page["slug"]):
            raise ValueError(f"Invalid page configuration: {page}")

    OUTPUT.mkdir(exist_ok=True)
    shutil.copytree(DOCS / "assets", OUTPUT / "assets", dirs_exist_ok=True)
    template = (DOCS / "page.html").read_text(encoding="utf-8")
    search = []
    for position, page in enumerate(pages):
        source_name = page["sources"][0]
        source = ROOT / source_name
        raw = source.read_text(encoding="utf-8")
        target = OUTPUT / "markdown" / source_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        text = raw
        if source.suffix != ".md":
            text = f"# {page['title']}\n\n```text\n{raw}\n```\n"
        # Authors use fenced display mathematics in both Markdown and HTML.
        text = re.sub(r"^```latex\s*\n(\$\$\n[\s\S]*?\n\$\$)\s*\n```", r"\1", text, flags=re.M)
        rendered = markdown.markdown(
            text,
            extensions=["fenced_code", "tables", "toc", "sane_lists", "pymdownx.arithmatex"],
            extension_configs={"pymdownx.arithmatex": {"generic": True}},
        )
        article = BeautifulSoup(rendered, "html.parser")
        for code in article.select("table code"):
            code.string = code.get_text().replace(r"\|", "|")
        for element in article.select("a[href], img[src]"):
            attr = "href" if element.name == "a" else "src"
            link = urlsplit(element[attr])
            if link.scheme or link.netloc or not link.path:
                continue
            resolved = (source.parent / unquote(link.path)).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.is_file():
                raise ValueError(f"{source_name}: missing local target {element[attr]}")
            relative = resolved.relative_to(ROOT).as_posix()
            if relative in sources:
                destination = sources[relative] + ".html"
            else:
                destination = "markdown/" + relative
                copied = OUTPUT / destination
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resolved, copied)
            element[attr] = urlunsplit(("", "", destination, link.query, link.fragment))
        for pre in article.select("pre"):
            code = pre.find("code")
            if code is None:
                continue
            language = next(
                (
                    c.removeprefix("language-")
                    for c in code.get("class", [])
                    if c.startswith("language-")
                ),
                "text",
            )
            try:
                lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
            except ClassNotFound:
                lexer = TextLexer(stripnl=False, ensurenl=False)
            colored = BeautifulSoup(
                "<pre>" + highlight(code.get_text(), lexer, HtmlFormatter(nowrap=True)) + "</pre>",
                "html.parser",
            ).pre
            code.clear()
            code.extend(list(colored.contents))
            pre["class"] = ["highlight"]
            shell = article.new_tag("div", attrs={"class": "code-shell"})
            toolbar = article.new_tag("div", attrs={"class": "code-toolbar"})
            label = article.new_tag("span")
            label.string = language
            button = article.new_tag("button", attrs={"class": "copy-code", "type": "button"})
            button.string = "Copy"
            toolbar.extend([label, button])
            pre.wrap(shell)
            shell.insert(0, toolbar)
        toc = "".join(
            f'<a class="toc-level-{heading.name[1]}" href="#{html.escape(heading["id"])}">{html.escape(heading.get_text())}</a>'
            for heading in article.select("h2[id], h3[id]")
        )
        navigation = []
        for category in dict.fromkeys(p["category"] for p in pages):
            entries = "".join(
                f'<a class="{"active" if p == page else ""}" href="{p["slug"]}.html">{html.escape(p["title"])}</a>'
                for p in pages
                if p["category"] == category
            )
            navigation.append(
                f'<section class="nav-group"><h2>{html.escape(category)}</h2>{entries}</section>'
            )
        pager = []
        for index, direction in [(position - 1, "Previous"), (position + 1, "Next")]:
            if 0 <= index < len(pages):
                neighbor = pages[index]
                pager.append(
                    f'<a class="pager-card {direction.lower()}" href="{neighbor["slug"]}.html"><span>{direction}</span><strong>{html.escape(neighbor["title"])}</strong></a>'
                )
        replacements = {
            "title": html.escape(page["title"]),
            "slug": page["slug"],
            "category": html.escape(page["category"]),
            "content": str(article),
            "toc": toc,
            "navigation": "".join(navigation),
            "pager": "".join(pager),
            "markdown": "markdown/" + source_name,
            "version": project["version"],
        }
        output = re.sub(r"\{\{(\w+)\}\}", lambda match: replacements[match[1]], template)
        (OUTPUT / f"{page['slug']}.html").write_text(output, encoding="utf-8")
        search.append(
            {
                "title": page["title"],
                "category": page["category"],
                "url": page["slug"] + ".html",
                "headings": " ".join(h.get_text() for h in article.select("h2,h3,h4")),
                "text": article.get_text(" ", strip=True),
                "snippet": article.get_text(" ", strip=True)[:220],
            }
        )

    error_page = {
        **replacements,
        "title": "Page not found",
        "slug": "404",
        "category": "Documentation",
        "content": '<h1 id="page-not-found">Page not found</h1><p>The requested documentation page does not exist. Use the navigation, search, or <a href="manual.html">user manual</a> to continue.</p>',
        "toc": "",
        "navigation": replacements["navigation"].replace('class="active"', 'class=""'),
        "pager": "",
        "markdown": "markdown/docs/index.md",
    }
    error_html = re.sub(r"\{\{(\w+)\}\}", lambda match: error_page[match[1]], template)
    error_document = BeautifulSoup(error_html, "html.parser")
    error_document.select_one('link[rel="alternate"]').decompose()
    (OUTPUT / "404.html").write_text(str(error_document), encoding="utf-8")

    index = [
        "# QpiAI Opt",
        "",
        "> Python SDK for optimization modelling, validation, conversion, and explicit local or platform execution.",
        "",
        "Start with [automation.md](markdown/automation.md) for task selection, execution boundaries, and recovery procedures.",
        "",
        "## Documentation",
        "",
    ]
    index.extend(
        f"- [{p['title']}](markdown/{p['sources'][0]})"
        for p in pages
        if p["sources"][0].endswith(".md")
    )
    (OUTPUT / "llms.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    (OUTPUT / "assets/search-index.js").write_text(
        "window.__QP_DOCS_INDEX__=" + json.dumps(search, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    shutil.copyfile(ROOT / "CITATION.cff", OUTPUT / "markdown/CITATION.cff")
    manifest = {
        "package": project["name"],
        "version": project["version"],
        "python": project["requires-python"],
        "pages": pages,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources
        },
    }
    (OUTPUT / "build-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    errors = []
    documents = {
        path: BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        for path in OUTPUT.glob("*.html")
    }
    for path, document in documents.items():
        ids = [tag["id"] for tag in document.select("[id]")]
        if len(ids) != len(set(ids)):
            errors.append(f"{path.name}: duplicate HTML IDs")
        for element in document.select("[href], [src]"):
            target = urlsplit(element.get("href", element.get("src", "")))
            if target.scheme or target.netloc:
                continue
            destination = (path.parent / unquote(target.path)).resolve() if target.path else path
            if not destination.is_file():
                errors.append(f"{path.name}: missing {target.geturl()}")
            elif (
                target.fragment
                and destination in documents
                and documents[destination].find(id=unquote(target.fragment)) is None
            ):
                errors.append(f"{path.name}: missing fragment {target.geturl()}")
    if errors:
        raise ValueError("\n".join(errors))
    print(f"Built {len(pages)} pages; all local HTML links and fragments resolve.")


if __name__ == "__main__":
    main()

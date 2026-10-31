# Trafilatura Core

<table align="right">
  <tbody>
    <tr>
      <td>
        <img width="220" src="media/logo-opaque.svg" alt="Trafilatura Core" />
        <br />
        <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="license: Apache-2.0" /></a>
        <br />
        <a href="https://github.com/markdownee/trafilaturacore/actions/workflows/release-npm.yml"><img src="https://github.com/markdownee/trafilaturacore/actions/workflows/release-npm.yml/badge.svg" alt="release" /></a>
        <br />
        <a href="https://github.com/markdownee/trafilaturacore"><img src="https://img.shields.io/badge/languages-TypeScript%20%2B%20Python-blue.svg" alt="TypeScript and Python" /></a>
        <h3>Available as:</h3>
        <ul>
          <li>
            <strong><a href="https://www.trafilaturacore.com/">Online playground</a></strong>
          </li>
          <li>
            <strong><a href="https://www.npmjs.com/package/@markdownee/trafilaturacore">npm package</a></strong>
          </li>
          <li>
            <strong><a href="https://pypi.org/project/trafilaturacore/">Python library on PyPI</a></strong>
          </li>
        </ul>
        <h3>Docs</h3>
        <ul>
          <li><a href="https://www.trafilaturacore.com/help/getting-started/">Getting started</a></li>
          <li><a href="https://www.trafilaturacore.com/help/npm-cli/">CLI help</a></li>
          <li><a href="https://www.trafilaturacore.com/help/npm-library/">Library help</a></li>
          <li><a href="https://www.trafilaturacore.com/help/pypi/">Python library help</a></li>
        </ul>
        <h3>Social</h3>
        <ul>
          <li><a href="https://github.com/markdownee/trafilaturacore">Star us on GitHub</a></li>
          <li><a href="https://github.com/markdownee">Follow us on GitHub</a></li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

Trafilatura Core extracts main content by removing boilerplate from HTML documents.

- Two language versions — **TypeScript** and **Python**: available as a [TypeScript library on npm](https://www.npmjs.com/package/@markdownee/trafilaturacore) and a [Python library on PyPI](https://pypi.org/project/trafilaturacore/).

- Trafilatura Core is an **open-source extraction engine** based on [Trafilatura](https://github.com/adbar/trafilatura), implemented in TypeScript and Python. The TypeScript extraction core ports Trafilatura's `fast=True` path, with [go-trafilatura](https://github.com/markusmobius/go-trafilatura) as a DOM translation aid. Native Python translates that TypeScript implementation.

- The _Core_ in its name means it is reduced to one task: extracting main content by removing boilerplate. An optional Source URL supplies metadata and image-resolution context; it is _never fetched_. Use [Turndown](https://www.npmjs.com/package/turndown) for Markdown conversion and [Markdownee](https://www.markdownee.com/) for crawling live websites.

The npm package also provides a [CLI](https://www.trafilaturacore.com/help/npm-cli/) for supplied HTML. Both libraries return cleaned HTML, available page metadata, and diagnostic messages; Python provides library APIs only.

Choose precision, balanced, or recall extraction, or keep for whole-document
cleanup. Images, links, tables, and detected user-comment sections have separate
controls. Output is compact HTML; if formatting fails, the cleaned HTML is
returned with a warning.

Pass the cleaned HTML to your application or save it to a file with the CLI.
The [library reference](https://www.trafilaturacore.com/help/npm-library/) covers
custom cleaning rules and resource limits; the
[CLI guide](https://www.trafilaturacore.com/help/npm-cli/) covers file input,
standard input, and JSON results.

The [Python library](https://www.trafilaturacore.com/help/pypi/) exposes synchronous
`clean()` and asynchronous `aclean()` calls, without a product CLI. The two language
versions may differ in parsing, serialization, date and URL handling, diagnostics,
and resource limits; use the guide for your language when selecting options. Keep
the original HTML beside the extracted result when comparing precision, balanced,
and recall settings.

## Try the CLI

Use Node.js 22.22.2+ on 22.x, 24.15.0+ on 24.x, or 26+. Run this in a terminal:

```bash
page_html='<nav>Home</nav><article><h1>Reading saved pages</h1>
<p>Save the original HTML before cleaning a page. A local copy lets
you compare the extracted article with its navigation and footer.</p>
<p>Keep the source address beside the snapshot. It provides context
when relative image links need to be resolved after extraction.</p>
</article>'
printf '%s\n' "$page_html" |
  npx --package=@markdownee/trafilaturacore trafilaturacore
```

The command prints the article heading and paragraphs as cleaned HTML, without
the navigation. Diagnostics go to stderr. No page is fetched.

## Use a library

- [npm library](https://www.trafilaturacore.com/help/npm-library/) — install the
  package and call `await clean(html)` in JavaScript or TypeScript.
- [npm CLI](https://www.trafilaturacore.com/help/npm-cli/) — process files or stdin
  and write HTML or a JSON result.
- [Python library](https://www.trafilaturacore.com/help/pypi/) — call `clean()` or
  await `aclean()`; see the [shipped examples](./examples/pypi-library/).

Extraction is not a security boundary. Before rendering untrusted output,
apply sanitization appropriate to your output context and a Content Security
Policy. Remote links and images may remain in the result.

## Acknowledgements

- [Trafilatura](https://github.com/adbar/trafilatura) — original Python
  implementation by Adrien Barbaresi, whose extraction path this engine ports.
- [go-trafilatura](https://github.com/markusmobius/go-trafilatura) — Go port by
  Markus Mobius, used as a DOM translation aid.

The [About page](https://www.trafilaturacore.com/about/) explains the port's
`fast=True` scope and language differences.

## Support

Report problems in the
[issue tracker](https://github.com/markdownee/trafilaturacore/issues).

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See the compact
[third-party notices](THIRD-PARTY-NOTICES.txt).


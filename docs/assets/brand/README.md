# Hengmu visual assets

Hengmu is a 青野 open-source project for evidence-bound architecture design,
decisions, and governance. Current-state assessment and open or constrained
target design are equal entry paths; accepted decisions flow into remediation
or Greenfield planning and deterministic policy. This directory contains the
repository-ready visual system used by both README editions.

## Asset matrix

| Asset | English | 简体中文 | Formats |
| --- | --- | --- | --- |
| 青野 pure-white primary logo | `qingye-logo-primary.*` | `qingye-logo-primary.*` | SVG, PNG |
| 青野 blue wordmark | `qingye-wordmark-blue.*` | `qingye-wordmark-blue.*` | SVG, PNG |
| Project icon | `en/hengmu-icon.*` | `zh-CN/hengmu-icon.*` | SVG, PNG |
| Project banner | `en/hengmu-banner.*` | `zh-CN/hengmu-banner.*` | SVG, PNG |
| README editorial figures | `../../../assets/hengmu-readme-illustrations/en/` | `../../../assets/hengmu-readme-illustrations/zh-CN/` | SVG, PNG |
| Governance flow | `../../../diagrams/en/` | `../../../diagrams/zh-CN/` | Mermaid, Excalidraw, SVG, PNG |

The two icon files deliberately use the same language-neutral geometry while
carrying localized SVG titles and descriptions. Banners, editorial figures,
and diagrams localize all visible text.

## Brand source

- Source of truth: `liyanqing90/qingye-brand`
- Asset baseline: `v1.1 Refined`
- Pure-white logo source: `source/avatar/avatar-primary-accent.svg`
- Pure-white logo source blob: `1e49ff156820f96463e02438b9321eeeec691275`
- Blue wordmark source: `source/logo/wordmark-blue.svg`
- Blue wordmark source blob: `82fee6a2c6de87e9ff586c30c219d5e1bc6356c2`
- Brand idea: 理性结构中的持续进化
- Brand proposition: 在不确定中，持续构建。
- 青野 ink: `#161719`
- 青野 blue: `#173FBE`
- Warm white: `#F4F2EC`
- Neutral gray: `#6D7078`
- Hairline: `#D9D9D2`

`qingye-wordmark.svg`, `qingye-wordmark-blue.svg`, and
`qingye-logo-primary.svg` vendor the official vector content without changing
its shapes. The PNG files are deterministic exports. Localized banners use the
blue horizontal wordmark; the square, pure-white accent avatar remains
available for repository and package surfaces. They do not typeset a
Latin-script substitute or reuse the textured social export. Do not redraw,
rotate, restack, or alter the official wordmark.

The current upstream `source/logo/wordmark-accent.svg` is intentionally not
vendored: its visible `野` path contains anomalous coordinates (`510.256` and
`5187.67`) that distort the rendered wordmark. The valid official
`avatar-primary-accent.svg` provides the same restrained blue accent for the
square logo.

Hengmu's display type follows one neutral sans-serif system across locales:
PingFang SC (with system sans fallbacks) for Chinese and Helvetica Neue (with
system sans fallbacks) for English. The official 青野 glyphs remain vector
artwork, never a substitute font.

## Hengmu mark

The Hengmu icon combines two written ideas in one structural mark:

1. the brand-blue horizontal member is the explicit measuring beam, `衡`;
2. the ink-black spine and load paths abstract the timber structure, `木`.

The mark is intentionally joinery rather than a literal scale, shield,
building, or architecture diagram.

Its visual philosophy is documented in
[`design-philosophy.md`](design-philosophy.md). The SVG files are the
editable sources; PNG files are deterministic rendered exports.

## 青野 character

`source/qingye-character-reference.png` is the character anchor used in the
README illustrations. It derives only from the public 青野 avatar:
slightly wavy short hair, a forward-looking posture, a high-collar
brand-blue work jacket, and a restrained path motif. It does not claim to
reconstruct undisclosed facial identity.

The character is original to 青野. The illustration workflow and
hand-drawn editorial discipline were produced with the Ian Xiaohei
Illustrations Skill; Xiaohei itself is not used as Hengmu's public character.

## Usage

- Use `hengmu-icon.png` for square repository, release, or package surfaces.
- Use `hengmu-banner.png` for GitHub social preview and announcement covers.
- Treat `hengmu-banner` and `assets/hengmu-cola-cover/hengmu-cover` as one
  project-cover family. They share the same evidence-to-verification visual
  layer, Qingye identity, project copy, and Measured Horizon gesture; adapt the
  composition to 3:1 or 16:9 instead of cropping one output into the other.
- Prefer the locale matching the surrounding copy.
- Do not recolor individual elements, add gradients, or place the mark on a
  low-contrast photographic background.
- Preserve at least one-quarter of the icon width as clear space.

The software is licensed under the repository's MIT License. Use of the
青野 name or wordmark to imply endorsement is not granted.

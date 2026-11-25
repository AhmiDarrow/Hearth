//! Hearth's look: what the Python app's page drew (`hearth/ui/static/app.css`), as theme
//! tokens and style rules over RemedyUI's own sheet. The palettes are RemedyUI's (the same
//! RemedyAI colors the page used); the sizes, corners and spacing are the page's.

use remedy_ui::core::{Color, Insets};
use remedy_ui::layout::Length;
use remedy_ui::style::{
    BorderEdge, BorderSide, BorderStyle, LetterSpacing, LineHeight, Shadow, States, StylePatch,
    StyleSheet, TextAlign, TextTransform, Theme,
};
use remedy_ui::view::{Align, Justify};
use remedy_ui::{El, Ui};
use remedy_ui::widgets as w;

/// The typefaces Hearth can be drawn in, all Remedy's own and bundled: the setting's id, the
/// family, and the name shown. The first is the default.
pub const FONTS: [(&str, &str, &str); 4] = [
    ("", "Remedy Sans UI", "Remedy Sans UI"),
    ("remedy-sans", "Remedy Sans", "Remedy Sans"),
    ("ahmi-print", "AhmiPrint", "Ahmi Print"),
    ("ahmi-pixel", "Ahmi Pixel", "Ahmi Pixel"),
];

/// `t` drawn in the typeface with the id `font` (an unknown id is the default one). Letters a
/// face lacks come from Remedy Sans UI; paths, invites and sizes stay in Ahmi Print.
pub fn with_font(mut t: Theme, font: &str) -> Theme {
    let family = FONTS
        .iter()
        .find(|f| f.0 == font)
        .map_or("Remedy Sans UI", |f| f.1);
    t.font_families = vec![family.to_owned(), "Remedy Sans UI".to_owned()];
    t.font_families.dedup();
    t.mono_families = vec!["AhmiPrint".to_owned(), "Remedy Sans UI".to_owned()];
    t
}

/// `t` with the page's sizes: 16 px text, 36 px controls, its corner radii.
pub fn theme(mut t: Theme) -> Theme {
    t.type_scale.small = 12.0;
    t.type_scale.body = 16.0;
    t.type_scale.title = 17.0;
    t.spacing.control_height = 36.0;
    t.radii.small = 9.0;
    t.radii.medium = 11.0;
    t.radii.large = 18.0;
    t
}

fn pad(v: f32, h: f32) -> Insets {
    Insets::symmetric(v, h)
}

fn sides(top: f32, end: f32, bottom: f32, start: f32) -> Insets {
    Insets {
        top,
        end,
        bottom,
        start,
    }
}

/// The style sheet for `t`: RemedyUI's, then the page's rules.
#[allow(clippy::too_many_lines)]
pub fn sheet(t: &Theme) -> StyleSheet {
    let mut s = remedy_ui::themes::sheet(t);
    let d = t.derive();
    let c = &t.colors;
    // The page's variables: --bg, --panel, --panel2 (and --bg2, the same color), --line.
    let (bg, panel, panel2, line) = (c.bg, c.panel, c.card, d.line);
    let (ink, muted, accent) = (d.text, d.muted, c.accent);
    let accent2 = c
        .custom
        .get("accent-hover")
        .copied()
        .unwrap_or(d.accent_hover);
    // `color-mix(in srgb, a p%, b)`.
    let mix = |a: Color, p: f32, b: Color| b.mix_oklab(a, p);
    let glow = Shadow::new(0.0, 0.0, 0.0, accent.with_alpha(0.22)).spread(3.0);
    let drop = Shadow::new(0.0, 18.0, 50.0, ink.with_alpha(0.16));
    let link = mix(accent, 0.9, ink);
    let disabled = mix(ink, 0.65, bg);
    let hover_fill = mix(panel2, 0.85, bg);
    let p = StylePatch::default;
    // The page's `--accent-hover-fg`: text that still reads on the darker hover fill.
    let on_accent2 = remedy_ui::style::ensure_contrast(d.on_accent, accent2, 4.5);
    // Accent text and icons on a panel (initials, a file's icon), readable in every palette.
    let accent_ink = remedy_ui::style::ensure_contrast(accent, panel2, 4.5);
    let under = |color: Color| BorderSide::new(1.0, color);
    let mut rule = |w: &'static str, v: Option<&'static str>, st: States, patch: StylePatch| {
        s.rule(w, v, st, patch);
    };

    // .btn
    for w in ["button", "icon_button"] {
        rule(
            w,
            None,
            States::NONE,
            p().background(panel2)
                .foreground(ink)
                .border(line)
                .border_width(1.0)
                .radius(11.0)
                .padding(pad(7.0, 14.0))
                .weight(400.0),
        );
        rule(w, None, States::HOVER, p().border(mix(accent, 0.45, line)));
        rule(w, None, States::PRESSED, p().background(bg));
        rule(
            w,
            None,
            States::DISABLED,
            p().opacity(1.0).foreground(disabled),
        );
        rule(
            w,
            Some("primary"),
            States::NONE,
            p().background(accent)
                .foreground(d.on_accent)
                .border(accent)
                .weight(650.0),
        );
        rule(
            w,
            Some("primary"),
            States::HOVER,
            p().background(accent2)
                .foreground(on_accent2)
                .border(accent2)
                .shadow(glow),
        );
        rule(
            w,
            Some("quiet"),
            States::NONE,
            p().background(Color::TRANSPARENT).border(line),
        );
        rule(
            w,
            Some("quiet"),
            States::HOVER,
            p().background(panel2.with_alpha(0.8)),
        );
        rule(w, Some("danger"), States::NONE, p().border(line));
    }
    rule(
        "icon_button",
        None,
        States::NONE,
        p().padding(Insets::all(7.5)).radius(11.0),
    );
    // .btn.primary.xl
    rule(
        "button",
        Some("xl"),
        States::NONE,
        p().background(accent)
            .foreground(d.on_accent)
            .border(accent)
            .weight(650.0)
            .radius(14.0)
            .padding(pad(13.0, 16.0)),
    );
    rule(
        "button",
        Some("xl"),
        States::HOVER,
        p().background(accent2)
            .foreground(on_accent2)
            .border(accent2)
            .shadow(glow),
    );
    // A row that is one button (a PC, a theme, a library, a folder): no frame of its own.
    rule(
        "button",
        Some("bare"),
        States::NONE,
        p().background(Color::TRANSPARENT)
            .border(Color::TRANSPARENT)
            .border_width(0.0)
            .padding(Insets::ZERO)
            .radius(12.0),
    );
    // select
    rule(
        "button",
        Some("select"),
        States::NONE,
        p().radius(12.0).padding(pad(10.0, 12.0)),
    );
    rule(
        "button",
        Some("select_small"),
        States::NONE,
        p().radius(12.0).padding(pad(5.0, 8.0)),
    );
    // .share-tab
    for (v, fg, fill, edge) in [
        ("pill", muted, Color::TRANSPARENT, line),
        ("pill_on", ink, panel2, mix(accent, 0.5, line)),
    ] {
        rule(
            "button",
            Some(v),
            States::NONE,
            p().background(fill)
                .foreground(fg)
                .border(edge)
                .radius(999.0)
                .padding(pad(4.0, 10.0))
                .weight(400.0),
        );
    }
    // .settings-nav button
    for (v, fg, fill) in [("nav", muted, Color::TRANSPARENT), ("nav_on", ink, panel2)] {
        rule(
            "button",
            Some(v),
            States::NONE,
            p().background(fill)
                .foreground(fg)
                .border(Color::TRANSPARENT)
                .radius(9.0)
                .padding(pad(8.0, 12.0))
                .weight(650.0),
        );
        rule(
            "button",
            Some(v),
            States::HOVER,
            p().background(panel2)
                .foreground(ink)
                .border(Color::TRANSPARENT),
        );
    }

    // input, textarea, select
    for w in ["text_input", "secret_input", "text_area", "search"] {
        rule(
            w,
            None,
            States::NONE,
            p().background(panel2)
                .foreground(ink)
                .border(line)
                .border_width(1.0)
                .radius(12.0)
                .padding(pad(10.0, 12.0)),
        );
        rule(
            w,
            None,
            States::FOCUS,
            p().border(mix(accent, 0.55, line)).shadow(glow),
        );
    }
    rule(
        "search",
        None,
        States::NONE,
        p().radius(999.0).padding(pad(8.0, 14.0)),
    );
    rule(
        "search",
        None,
        States::FOCUS_WITHIN,
        p().border(mix(accent, 0.55, line)).shadow(glow),
    );
    // .composer input
    rule(
        "text_input",
        Some("chat"),
        States::NONE,
        p().radius(14.0).padding(pad(12.0, 14.0)),
    );
    // label
    rule(
        "field",
        None,
        States::NONE,
        p().foreground(muted).text_size(12.0).weight(650.0),
    );

    // Text. `.hint`, `.meta b`, `.meta span`, `.col-head`, `.brand small`, `.mono`.
    let hint = p()
        .foreground(muted)
        .text_size(12.0)
        .line_height(LineHeight::Relative(1.45));
    rule("label", Some("small"), States::NONE, hint.clone());
    rule("label", Some("hint"), States::NONE, hint);
    rule(
        "label",
        Some("lead"),
        States::NONE,
        p().foreground(muted)
            .text_size(14.0)
            .line_height(LineHeight::Relative(1.45)),
    );
    rule(
        "label",
        Some("name"),
        States::NONE,
        p().text_size(13.0).weight(650.0),
    );
    rule(
        "label",
        Some("tile_name"),
        States::NONE,
        p().text_size(13.0)
            .weight(650.0)
            .text_align(TextAlign::Center),
    );
    rule(
        "label",
        Some("label"),
        States::NONE,
        p().foreground(muted).text_size(12.0).weight(650.0),
    );
    rule(
        "label",
        Some("check"),
        States::NONE,
        p().text_size(12.0).weight(650.0),
    );
    for w in ["label", "heading"] {
        rule(
            w,
            Some("title"),
            States::NONE,
            p().text_size(17.0)
                .weight(650.0)
                .letter_spacing(LetterSpacing::Em(-0.03)),
        );
    }
    rule(
        "label",
        Some("chrome"),
        States::NONE,
        p().text_size(13.0)
            .weight(650.0)
            .letter_spacing(LetterSpacing::Em(-0.02)),
    );
    for (v, spacing, weight) in [("caps", 0.08, 600.0), ("colhead", 0.12, 650.0)] {
        rule(
            "label",
            Some(v),
            States::NONE,
            p().foreground(muted)
                .text_size(11.0)
                .weight(weight)
                .letter_spacing(LetterSpacing::Em(spacing))
                .text_transform(TextTransform::Upper),
        );
    }
    rule(
        "label",
        Some("mono"),
        States::NONE,
        p().mono(true).text_size(11.0),
    );
    rule(
        "label",
        Some("size"),
        States::NONE,
        p().mono(true).foreground(muted).text_size(12.0),
    );
    // input[readonly]
    rule(
        "label",
        Some("readonly"),
        States::NONE,
        p().background(panel2)
            .border(line)
            .border_width(1.0)
            .radius(12.0)
            .padding(pad(10.0, 12.0)),
    );
    // input[type=checkbox]: a small square box.
    rule("check", None, States::NONE, p().radius(4.0));
    // label.row.check
    rule(
        "choice",
        None,
        States::NONE,
        p().text_size(12.0).weight(650.0),
    );
    // .invite, .path-display
    rule(
        "label",
        Some("mono_box"),
        States::NONE,
        p().mono(true)
            .text_size(11.0)
            .background(panel2)
            .border(line)
            .border_width(1.0)
            .border_style(BorderStyle::Dashed)
            .radius(12.0)
            .padding(Insets::all(10.0)),
    );
    // h2, h3 of the settings and the dialogs.
    rule(
        "heading",
        Some("2"),
        States::NONE,
        p().heading(true).text_size(17.0).weight(700.0),
    );
    rule(
        "heading",
        Some("3"),
        States::NONE,
        p().heading(true).text_size(16.0).weight(700.0),
    );
    rule(
        "empty_state",
        None,
        States::NONE,
        p().foreground(muted)
            .padding(pad(28.0, 18.0))
            .line_height(LineHeight::Relative(1.5)),
    );

    // .tab
    rule(
        "tab",
        None,
        States::NONE,
        p().foreground(muted)
            .radius(9.0)
            .padding(pad(7.0, 12.0))
            .weight(600.0),
    );
    rule(
        "tab",
        None,
        States::HOVER,
        p().foreground(ink).background(Color::TRANSPARENT),
    );
    rule(
        "tab",
        None,
        States::SELECTED,
        p().foreground(ink).background(panel2).weight(600.0),
    );
    // .seg, .seg-btn
    rule(
        "segmented",
        None,
        States::NONE,
        p().background(Color::TRANSPARENT)
            .border(line)
            .border_width(1.0)
            .radius(10.0)
            .padding(Insets::ZERO),
    );
    rule(
        "segment",
        None,
        States::NONE,
        p().foreground(muted)
            .radius(9.0)
            .padding(pad(6.0, 10.0))
            .text_size(12.0)
            .weight(650.0),
    );
    rule(
        "segment",
        None,
        States::HOVER,
        p().foreground(ink).background(Color::TRANSPARENT),
    );
    rule(
        "segment",
        None,
        States::SELECTED,
        p().background(panel2).foreground(ink),
    );
    // .crumb
    rule(
        "breadcrumbs",
        None,
        States::NONE,
        p().foreground(link).text_size(13.0).weight(650.0),
    );

    // .app-menu, .ctx-menu
    rule(
        "menu",
        None,
        States::NONE,
        p().background(panel)
            .border(line)
            .border_width(1.0)
            .radius(12.0)
            .padding(Insets::all(6.0))
            .shadow(drop),
    );
    rule(
        "menu_item",
        None,
        States::NONE,
        p().radius(8.0).padding(pad(8.0, 10.0)),
    );
    for st in [States::FOCUS, States::FOCUS_VISIBLE, States::SELECTED] {
        rule(
            "menu_item",
            None,
            st,
            p().background(mix(accent, 0.14, panel2)),
        );
    }
    // .toast
    rule(
        "toast",
        None,
        States::NONE,
        p().background(panel)
            .foreground(ink)
            .border(line)
            .border_width(1.0)
            .radius(12.0)
            .padding(pad(10.0, 14.0))
            .shadow(drop),
    );
    // .modal-backdrop, .modal, .settings-shell
    rule(
        "scrim",
        None,
        States::NONE,
        p().background(Color::BLACK.with_alpha(0.45)),
    );
    rule(
        "dialog",
        None,
        States::NONE,
        p().background(panel)
            .border(line)
            .border_width(1.0)
            .radius(18.0)
            .padding(Insets::all(18.0))
            .shadow(drop),
    );
    // A dialog that lays out its own head, body and foot.
    rule(
        "dialog",
        Some("bare"),
        States::NONE,
        p().padding(Insets::ZERO),
    );
    rule(
        "modal_head",
        None,
        States::NONE,
        p().padding(sides(16.0, 18.0, 0.0, 18.0)),
    );
    rule(
        "modal_body",
        None,
        States::NONE,
        p().padding(pad(12.0, 18.0)),
    );
    rule(
        "modal_foot",
        None,
        States::NONE,
        p().padding(sides(12.0, 18.0, 16.0, 18.0))
            .border_side(BorderEdge::Top, under(line)),
    );
    // The "Built with RemedyUI" badge in About: a pill in the accent, as on the README.
    rule(
        "badge",
        None,
        States::NONE,
        p().padding(pad(4.0, 10.0)).text_size(12.0).weight(700.0),
    );
    // .bar
    rule(
        "progress",
        None,
        States::NONE,
        p().background(panel2).foreground(accent).radius(999.0),
    );
    // .transfer.done .bar, .transfer.err .bar
    rule("progress", Some("done"), States::NONE, p().foreground(c.ok));
    rule(
        "progress",
        Some("error"),
        States::NONE,
        p().foreground(c.danger),
    );
    // .dot.on
    rule("lamp", None, States::NONE, p().foreground(c.ok));

    // .chrome, .topbar, .update-banner
    rule(
        "chrome",
        None,
        States::NONE,
        p().background(mix(panel, 0.92, bg))
            .padding(pad(4.0, 8.0))
            .border_side(BorderEdge::Bottom, under(line)),
    );
    rule(
        "topbar",
        None,
        States::NONE,
        p().background(mix(panel, 0.88, bg))
            .padding(pad(10.0, 16.0))
            .border_side(BorderEdge::Bottom, under(line)),
    );
    rule(
        "update_banner",
        None,
        States::NONE,
        p().background(mix(panel, 0.9, accent))
            .text_size(12.0)
            .border_side(BorderEdge::Bottom, under(line)),
    );
    // .sidebar, .col-head, .list, .member, .share-card
    rule(
        "sidebar",
        None,
        States::NONE,
        p().background(mix(panel, 0.7, bg))
            .border_side(BorderEdge::End, under(line)),
    );
    rule(
        "col_head",
        None,
        States::NONE,
        p().padding(sides(18.0, 18.0, 8.0, 18.0)),
    );
    rule(
        "side_list",
        None,
        States::NONE,
        p().padding(sides(4.0, 10.0, 16.0, 10.0)),
    );
    for w in ["member", "share_card"] {
        rule(
            w,
            None,
            States::NONE,
            p().padding(pad(9.0, 10.0))
                .radius(12.0)
                .border(Color::TRANSPARENT)
                .border_width(1.0),
        );
        rule(
            w,
            None,
            States::HOVER,
            p().background(hover_fill).border(line),
        );
        rule(
            w,
            None,
            States::DROP_TARGET,
            p().background(accent.with_alpha(0.1))
                .border(mix(accent, 0.7, line))
                .border_width(2.0)
                .border_style(BorderStyle::Dashed),
        );
    }
    rule(
        "member",
        None,
        States::SELECTED,
        p().background(mix(accent, 0.12, panel2))
            .border(mix(accent, 0.35, line)),
    );
    // .avatar
    rule(
        "avatar_box",
        None,
        States::NONE,
        p().background(panel2)
            .foreground(accent_ink)
            .border(line)
            .border_width(1.0)
            .radius(10.0)
            .text_size(12.0)
            .weight(700.0),
    );
    rule("avatar_box", Some("tile"), States::NONE, p().radius(14.0));

    // .tabs, .files-toolbar, .crumb, .files
    rule(
        "tabs_bar",
        None,
        States::NONE,
        p().padding(sides(12.0, 18.0, 0.0, 18.0)),
    );
    rule(
        "files_toolbar",
        None,
        States::NONE,
        p().padding(sides(10.0, 18.0, 0.0, 18.0)),
    );
    rule(
        "crumb_bar",
        None,
        States::NONE,
        p().padding(sides(8.0, 20.0, 4.0, 20.0)),
    );
    // .file-tile and .file-row: the grid's tile boxes.
    for (v, fill) in [("card", panel), ("row", Color::TRANSPARENT)] {
        rule(
            "grid_tile",
            Some(v),
            States::NONE,
            p().background(fill)
                .radius(12.0)
                .border(Color::TRANSPARENT)
                .border_width(1.0),
        );
        rule(
            "grid_tile",
            Some(v),
            States::HOVER,
            p().background(hover_fill).border(line),
        );
        rule(
            "grid_tile",
            Some(v),
            States::SELECTED,
            p().background(mix(accent, 0.16, panel2))
                .border(mix(accent, 0.5, line)),
        );
    }
    // .drop-hint, .drop-card
    rule(
        "drop_hint",
        None,
        States::NONE,
        p().foreground(muted)
            .border(line)
            .border_width(1.0)
            .border_style(BorderStyle::Dashed)
            .radius(16.0)
            .padding(pad(28.0, 16.0))
            .text_align(TextAlign::Center),
    );
    rule(
        "drop_card",
        None,
        States::NONE,
        p().background(panel2)
            .border(line)
            .border_width(1.0)
            .radius(16.0)
            .padding(pad(18.0, 22.0))
            .weight(650.0),
    );
    // .transfers
    rule(
        "transfers",
        None,
        States::NONE,
        p().padding(sides(8.0, 16.0, 12.0, 16.0))
            .text_size(12.0)
            .border_side(BorderEdge::Top, under(line)),
    );

    // .chat-log, .bubble, .composer
    rule("chat_log", None, States::NONE, p().padding(pad(18.0, 22.0)));
    rule(
        "bubble",
        None,
        States::NONE,
        p().background(panel)
            .border(line)
            .border_width(1.0)
            .radius(16.0)
            .padding(pad(10.0, 13.0)),
    );
    rule(
        "bubble",
        Some("me"),
        States::NONE,
        p().background(accent).foreground(d.on_accent),
    );
    // `.who` and `.when`: small, and muted on another PC's bubble. On this PC's own they keep
    // the bubble's text color: the page's muted gray did not read on the accent.
    for (v, weight, quiet) in [
        ("bubble_who", 650.0, true),
        ("bubble_when", 400.0, true),
        ("bubble_who_me", 650.0, false),
        ("bubble_when_me", 400.0, false),
    ] {
        let size = p().text_size(11.0).weight(weight);
        rule(
            "label",
            Some(v),
            States::NONE,
            if quiet { size.foreground(muted) } else { size },
        );
    }
    rule(
        "composer",
        None,
        States::NONE,
        p().background(mix(panel, 0.7, bg))
            .padding(sides(14.0, 18.0, 18.0, 18.0))
            .border_side(BorderEdge::Top, under(line)),
    );

    // .gate-card, .nearby, .more
    rule(
        "gate_card",
        None,
        States::NONE,
        p().background(mix(panel, 0.92, bg))
            .border(line)
            .border_width(1.0)
            .radius(24.0)
            .padding(sides(32.0, 28.0, 24.0, 28.0))
            .shadow(drop),
    );
    rule(
        "nearby",
        None,
        States::NONE,
        p().background(panel2)
            .border(line)
            .border_width(1.0)
            .radius(14.0)
            .padding(Insets::all(12.0)),
    );
    rule(
        "disclosure",
        None,
        States::NONE,
        p().padding(sides(10.0, 0.0, 0.0, 0.0))
            .border_side(BorderEdge::Top, under(line)),
    );
    rule(
        "disclosure_head",
        None,
        States::NONE,
        p().foreground(muted)
            .text_size(13.0)
            .weight(600.0)
            .padding(Insets::ZERO),
    );

    // .settings-head, .settings-nav, .settings-section, .settings-block
    rule(
        "settings_head",
        None,
        States::NONE,
        p().padding(pad(12.0, 16.0))
            .border_side(BorderEdge::Bottom, under(line)),
    );
    rule(
        "settings_nav",
        None,
        States::NONE,
        p().background(mix(panel2, 0.7, panel))
            .padding(pad(10.0, 8.0))
            .border_side(BorderEdge::End, under(line)),
    );
    rule(
        "settings_section",
        None,
        States::NONE,
        p().padding(sides(20.0, 22.0, 28.0, 22.0)),
    );
    rule(
        "settings_block",
        None,
        States::NONE,
        p().padding(pad(12.0, 0.0))
            .border_side(BorderEdge::Bottom, under(line)),
    );
    rule(
        "settings_block",
        Some("last"),
        States::NONE,
        p().border_side(BorderEdge::Bottom, BorderSide::new(0.0, Color::TRANSPARENT)),
    );
    // .theme-card, .quick-share, .settings-share, .swatch
    for (w, radius, padding) in [
        ("theme_card", 12.0, pad(9.0, 10.0)),
        ("quick_share", 14.0, Insets::all(12.0)),
    ] {
        rule(
            w,
            None,
            States::NONE,
            p().background(panel2)
                .border(line)
                .border_width(1.0)
                .radius(radius)
                .padding(padding),
        );
        rule(w, None, States::HOVER, p().border(accent).shadow(glow));
        rule(w, None, States::SELECTED, p().border(accent).shadow(glow));
    }
    rule(
        "settings_share",
        None,
        States::NONE,
        p().background(panel2)
            .border(line)
            .border_width(1.0)
            .radius(12.0)
            .padding(Insets::all(10.0)),
    );
    rule(
        "swatch",
        None,
        States::NONE,
        p().border(Color::BLACK.with_alpha(0.13))
            .border_width(1.0)
            .radius(7.0),
    );
    // .folder-list, .folder-item
    rule(
        "folder_list",
        None,
        States::NONE,
        p().background(panel2)
            .border(line)
            .border_width(1.0)
            .radius(14.0),
    );
    rule(
        "folder_item",
        None,
        States::NONE,
        p().padding(pad(9.0, 12.0))
            .border_side(BorderEdge::Bottom, under(line)),
    );
    for st in [States::HOVER, States::SELECTED] {
        rule(
            "folder_item",
            None,
            st,
            p().background(mix(panel, 0.2, panel2)),
        );
    }
    s
}

/// The page's `.avatar`: a small framed box holding initials or an icon.
pub fn avatar_box(ui: &Ui, size: f32, content: El) -> El {
    ui.el("avatar_box")
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .width(size)
        .height(size)
        .layout(|l| l.shrink = 0.0)
        .child(content)
}

/// Initials as the page made them: the first letters of the first two words.
pub fn initials(name: &str) -> String {
    name.split_whitespace()
        .take(2)
        .filter_map(|p| p.chars().next())
        .flat_map(char::to_uppercase)
        .collect()
}

/// The page's `.meta`: a name over a muted line, each cut short with an ellipsis.
pub fn meta(ui: &Ui, name: impl Into<String>, sub: impl Into<String>) -> El {
    w::column(ui)
        .grow(1.0)
        .layout(|l| l.min_width = Length::Px(0.0))
        .child(w::text(ui, name.into()).variant("name").truncate())
        .child(w::text(ui, sub.into()).variant("small").truncate())
}

#[cfg(test)]
mod tests {
    use remedy_ui::style::{States, StyleKey, StylePatch};

    #[test]
    fn every_typeface_offered_is_bundled_and_the_default_is_remedy_sans_ui() {
        let bundled: Vec<&str> = remedy_ui_fonts::all().iter().map(|f| f.family).collect();
        for (id, family, _) in super::FONTS {
            assert!(bundled.contains(&family), "{family} is in the program");
            let t = super::with_font(crate::shell::theme_for("dark", None, false), id);
            assert_eq!(t.font_families.first().map(String::as_str), Some(family));
        }
        let t = super::with_font(crate::shell::theme_for("dark", None, false), "gone");
        assert_eq!(t.font_families, ["Remedy Sans UI"]);
        assert_eq!(
            t.mono_families.first().map(String::as_str),
            Some("AhmiPrint")
        );
    }

    #[test]
    fn initials_are_the_first_letters_of_two_words() {
        assert_eq!(super::initials("studio pc upstairs"), "SP");
        assert_eq!(super::initials("Den"), "D");
        assert_eq!(super::initials(""), "");
    }

    /// Every palette keeps text readable on the page's fills: a primary button, the user's own
    /// chat bubble, a selected file.
    #[test]
    fn text_stays_readable_on_the_pages_fills_in_every_palette() {
        for id in crate::settings::THEMES {
            let t = super::theme(crate::shell::theme_for(id, Some(true), false));
            let mut sheet = super::sheet(&t);
            for (widget, variant, states) in [
                ("button", "primary", States::NONE),
                ("button", "xl", States::HOVER),
                ("bubble", "me", States::NONE),
                ("grid_tile", "card", States::SELECTED),
                ("member", "", States::SELECTED),
            ] {
                let key = StyleKey {
                    widget,
                    variant,
                    states,
                };
                let r = sheet.resolve(&key, &StylePatch::defaults(&t), &StylePatch::default());
                let ratio = r.foreground.contrast(r.background);
                assert!(
                    ratio >= 4.5,
                    "{id}: {widget} {variant} text is {ratio:.2}:1 on its fill"
                );
            }
        }
    }
}

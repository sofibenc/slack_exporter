from slack_exporter.formatting import FormatContext, emoji_to_unicode, mrkdwn_to_html, mrkdwn_to_text

CTX = FormatContext(users={"U1": "Alice"}, channels={"C1": "general"})


def fmt(text: str) -> str:
    return mrkdwn_to_html(text, CTX)


def test_plain_text_unchanged():
    assert fmt("bonjour") == "bonjour"


def test_slack_entities_are_not_double_escaped():
    # Slack envoie « a & b <tag> » sous la forme « a &amp; b &lt;tag&gt; ».
    assert fmt("a &amp; b &lt;tag&gt;") == "a &amp; b &lt;tag&gt;"


def test_literal_entity_typed_by_user_is_preserved():
    # L'utilisateur a tapé « &lt; » : Slack l'envoie « &amp;lt; ».
    assert fmt("&amp;lt;") == "&amp;lt;"


def test_raw_html_cannot_be_injected():
    out = fmt("<script>alert(1)</script> &lt;img src=x onerror=alert(1)&gt;")
    assert "<script" not in out
    assert "<img" not in out


def test_newlines_become_br():
    assert fmt("a\nb") == "a<br>b"


def test_bold_italic_strike():
    assert fmt("*gras* _ital_ ~barré~") == "<strong>gras</strong> <em>ital</em> <del>barré</del>"


def test_underscores_inside_words_are_not_italic():
    assert fmt("mon_nom_de_variable") == "mon_nom_de_variable"


def test_inline_code_is_not_formatted():
    assert fmt("`*pas gras*`") == "<code>*pas gras*</code>"


def test_code_block_keeps_newlines_and_unescapes_entities():
    assert fmt("```\nx = 1 &lt; 2\ny = *z*\n```") == "<pre><code>x = 1 &lt; 2\ny = *z*</code></pre>"


def test_blockquote():
    assert fmt("&gt; cité\n&gt; suite\nnormal") == "<blockquote>cité<br>suite</blockquote>normal"


def test_known_user_mention():
    assert fmt("<@U1> salut") == '<span class="mention">@Alice</span> salut'


def test_unknown_user_mention_falls_back_to_label_then_id():
    assert fmt("<@U9|bob>") == '<span class="mention">@bob</span>'
    assert fmt("<@U9>") == '<span class="mention">@U9</span>'


def test_exported_channel_is_linked():
    assert fmt("<#C1|general>") == '<a class="channel" href="../C1/index.html">#general</a>'


def test_non_exported_channel_is_plain_text():
    assert fmt("<#C2|secret>") == '<span class="channel">#secret</span>'


def test_special_mentions():
    assert fmt("<!here>") == '<span class="mention">@here</span>'
    assert fmt("<!channel>") == '<span class="mention">@channel</span>'
    assert fmt("<!subteam^S1|@devs>") == '<span class="mention">@devs</span>'


def test_date_token_uses_fallback_label():
    assert fmt("<!date^1392734382^{date}|13 fév. 2014>") == "13 fév. 2014"


def test_link_with_label_and_escaped_ampersand():
    assert fmt("<https://ex.com/a?b=1&amp;c=2|site>") == (
        '<a href="https://ex.com/a?b=1&amp;c=2" rel="noopener noreferrer">site</a>'
    )


def test_bare_link():
    assert fmt("<https://ex.com>") == '<a href="https://ex.com" rel="noopener noreferrer">https://ex.com</a>'


def test_mailto_link():
    assert fmt("<mailto:a@b.fr|a@b.fr>") == '<a href="mailto:a@b.fr" rel="noopener noreferrer">a@b.fr</a>'


def test_dangerous_scheme_is_not_linked():
    out = fmt("<javascript:alert(1)|clic>")
    assert out == "clic"


def test_link_label_is_escaped():
    assert fmt("<https://ex.com|a &lt;b&gt;>") == '<a href="https://ex.com" rel="noopener noreferrer">a &lt;b&gt;</a>'


def test_quote_in_url_cannot_break_out_of_attribute():
    out = fmt('<https://ex.com/" onmouseover="x|t>')
    assert out == '<a href="https://ex.com/&quot; onmouseover=&quot;x" rel="noopener noreferrer">t</a>'


def test_bold_around_mention():
    assert fmt("*<@U1>*") == '<strong><span class="mention">@Alice</span></strong>'


def test_emoji():
    assert fmt(":smile: :inconnu:") == "😄 :inconnu:"


def test_time_is_not_taken_for_emoji():
    assert fmt("rdv à 10:30:45") == "rdv à 10:30:45"


def test_emoji_to_unicode_ignores_skin_tone():
    assert emoji_to_unicode("+1::skin-tone-3") == "👍"
    assert emoji_to_unicode("perso") is None


def test_plain_text_for_search_resolves_mentions_and_drops_markup():
    text = "*Point* avec <@U1> sur <#C1|general> : <https://ex.com|la doc> &amp; `code`\n:smile:"
    assert mrkdwn_to_text(text, CTX) == "Point avec @Alice sur #general : la doc & code 😄"


def test_plain_text_keeps_html_like_content_as_text():
    assert mrkdwn_to_text("&lt;script&gt;", CTX) == "<script>"


def test_plain_text_separates_quotes_and_code_blocks_from_following_text():
    assert mrkdwn_to_text("&gt; cité\nsuite ```code```fin", CTX) == "cité suite code fin"

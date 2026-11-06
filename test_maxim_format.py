import unittest

from maxim_format import md_to_telegram_html as md


class MarkdownToHtmlTests(unittest.TestCase):
    def test_inline_styles(self):
        self.assertEqual(md("**жирный** и *курсив* и ~~нет~~"), "<b>жирный</b> и <i>курсив</i> и <s>нет</s>")
        self.assertEqual(md("__тоже жирный__ и _тоже курсив_"), "<b>тоже жирный</b> и <i>тоже курсив</i>")

    def test_plain_text_is_escaped(self):
        self.assertEqual(md("a < b && c > d. Ок!"), "a &lt; b &amp;&amp; c &gt; d. Ок!")

    def test_snake_case_and_math_stay_plain(self):
        self.assertEqual(md("my_var_name и 2*3*4"), "my_var_name и 2*3*4")

    def test_code_is_not_formatted_inside(self):
        self.assertEqual(md("`a_b*c* <x>`"), "<code>a_b*c* &lt;x&gt;</code>")
        self.assertEqual(
            md("Смотри:\n```cpp\nstd::vector<int> v; // **не жирный**\n```"),
            'Смотри:\n<pre><code class="language-cpp">std::vector&lt;int&gt; v; // **не жирный**</code></pre>',
        )

    def test_links_and_headings(self):
        self.assertEqual(
            md("# Итог\n[релиз 1.0](https://x.org/a?b=1&c=2)"),
            '<b>Итог</b>\n<a href="https://x.org/a?b=1&amp;c=2">релиз 1.0</a>',
        )


if __name__ == "__main__":
    unittest.main()

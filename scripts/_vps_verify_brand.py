from app.bot import formatting

print("file=", formatting.__file__)
src = open(formatting.__file__, encoding="utf-8").read()
print("has_brand=", "Alpha Trade Oracle" in src)
print("signal_has_brand_line=", '*escape_markdown_v2(\'Alpha Trade Oracle\')*' in src or "Alpha Trade Oracle" in src.split("format_signal_message")[1][:500])

import pymupdf


class Probe:
    name = "probe"
    model = "probe"

    def __init__(self, fn):
        self.fn = fn

    def translate_text(self, source, *, lang_in, lang_out, instruction):
        return self.fn(source)


def write_lines(path, pages):
    document = pymupdf.open()
    for lines in pages:
        page = document.new_page()
        y = 96
        for line in lines:
            page.insert_text((72, y), line, fontsize=14, fontname="helv")
            y += 28
    document.save(path)
    document.close()


def write_formula_pdf(path):
    document = pymupdf.open()
    page = document.new_page()
    size = 16
    y = 120
    x = 72
    helv = pymupdf.Font("helv")
    times = pymupdf.Font("tiro")
    page.insert_text((x, y), "Energy ", fontsize=size, fontname="helv")
    x += helv.text_length("Energy ", fontsize=size)
    page.insert_text((x, y), "E=mc^2", fontsize=size, fontname="tiro")
    x += times.text_length("E=mc^2", fontsize=size)
    page.insert_text((x, y), " remains", fontsize=size, fontname="helv")
    document.save(path)
    document.close()

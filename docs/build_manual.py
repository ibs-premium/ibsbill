"""Build the Word and PDF copies of the staff manual from STAFF_MANUAL.md.

    python docs/build_manual.py

STAFF_MANUAL.md is the one source to edit. This script writes
eBilling_MoMo_Staff_Manual.docx (python-docx) and eBilling_MoMo_Staff_Manual.pdf (Microsoft Edge
or Google Chrome, run headless), so both are always in step with the markdown.
"""
import base64
import html
import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
SOURCE = DOCS / 'STAFF_MANUAL.md'
IMG = ROOT / 'static' / 'dashboard' / 'img'
LOGO = IMG / 'ibs_logo_white_cropped.png'
APP_ICON = IMG / 'ebilling_logo.jpg'

TITLE = 'Staff User Manual'
PRODUCT = 'eBilling MoMo Gateway'
SUBTITLE = ('How to take MTN Mobile Money payments, follow up payments that are stuck, '
            'and produce reports from the gateway dashboard.')
ADDRESS = '/dashboard/ on the gateway server'
VERSION = 'Version 1.2 · November 2026'
RIGHTS = 'All rights reserved. www.ibsgh.com'
GREEN = '017A3D'
GREEN_DARK = '015C2E'
COVER = '019E4F'  # matches the background of the white logo image


# ---------------------------------------------------------------- parsing

def parse(markdown):
    """Markdown subset -> list of blocks: (kind, data)."""
    blocks, lines, i = [], markdown.split('\n'), 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip() == '---':
            i += 1
        elif line.startswith('# '):
            i += 1  # document title: the cover page carries it
        elif line.startswith('## '):
            blocks.append(('h1', line[3:].strip()))
            i += 1
        elif line.startswith('### '):
            blocks.append(('h2', line[4:].strip()))
            i += 1
        elif line.startswith('```'):
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i])
                i += 1
            blocks.append(('code', '\n'.join(code)))
            i += 1
        elif line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', c) for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(('table', rows))
        elif re.match(r'^\s*(- |\d+\. )', line):
            items = []
            while i < len(lines) and re.match(r'^\s*(- |\d+\. )', lines[i]):
                m = re.match(r'^(\s*)(- |(\d+)\. )(.*)$', lines[i])
                items.append((len(m.group(1)) // 3, m.group(3), m.group(4)))
                i += 1
            blocks.append(('list', items))
        else:
            para = [line.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r'^(#|\||```|\s*(- |\d+\. ))', lines[i]):
                para.append(lines[i].strip())
                i += 1
            blocks.append(('p', ' '.join(para)))
    return blocks


INLINE = re.compile(r'(\*\*.+?\*\*|\*[^*\s][^*]*?\*|`[^`]+`|\[[^\]]+\]\([^)]+\))')


def inline(text):
    """Split text into (kind, text) runs: plain, bold, italic, code."""
    runs = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith('**'):
            runs.append(('bold', part[2:-2]))
        elif part.startswith('`'):
            runs.append(('code', part[1:-1]))
        elif part.startswith('*') and part.endswith('*') and len(part) > 2:
            runs.append(('italic', part[1:-1]))
        elif part.startswith('['):
            runs.append(('plain', re.match(r'\[([^\]]+)\]', part).group(1)))
        else:
            runs.append(('plain', part))
    return runs


def strip_inline(text):
    return ''.join(t for _, t in inline(text))


# ---------------------------------------------------------------- HTML / PDF

CSS = f"""
@page {{ size: A4; margin: 18mm 16mm 20mm; @bottom-center {{ content: counter(page); font: 9pt 'Segoe UI', sans-serif; color: #6b7280; }} }}
@page :first {{ margin: 0; @bottom-center {{ content: ''; }} }}
* {{ box-sizing: border-box; }}
body {{ font: 10.5pt/1.55 'Segoe UI', 'Inter', Arial, sans-serif; color: #1f2937; margin: 0; }}
.cover {{ background: #{COVER}; color: #fff; height: 297mm; padding: 22mm 20mm; page-break-after: always;
  display: flex; flex-direction: column; }}
.cover .logo {{ height: 14mm; align-self: flex-start; }}
.cover .mid {{ margin-top: auto; margin-bottom: auto; }}
.cover .icon {{ width: 24mm; height: 24mm; border-radius: 6mm; margin-bottom: 8mm; }}
.cover .product {{ font-size: 13pt; font-weight: 600; }}
.cover h1 {{ font-size: 36pt; margin: 3mm 0 5mm; line-height: 1.1; color: #fff; border: 0; padding: 0; }}
.cover .sub {{ font-size: 12pt; max-width: 150mm; opacity: .95; }}
.cover .chip {{ display: inline-block; margin-top: 7mm; padding: 1.5mm 4mm; border: 1px solid rgba(255,255,255,.5);
  border-radius: 8mm; font-size: 9pt; }}
.cover .foot {{ font-size: 10.5pt; line-height: 1.6; }}
.toc {{ page-break-after: always; }}
.toc h2 {{ color: #{GREEN_DARK}; font-size: 20pt; margin: 0 0 6mm; }}
.toc ol {{ list-style: none; padding: 0; margin: 0; }}
.toc li {{ padding: 2.2mm 0; border-bottom: 1px solid #e5e7eb; font-size: 11pt; }}
h1 {{ color: #{GREEN_DARK}; font-size: 18pt; margin: 9mm 0 3mm; padding-top: 4mm; border-top: 1px solid #e5e7eb;
  page-break-after: avoid; }}
h1 small {{ display: block; font-size: 9pt; color: #{GREEN}; font-weight: 600; letter-spacing: .08em; }}
h2 {{ font-size: 12.5pt; margin: 6mm 0 2mm; color: #1f2937; page-break-after: avoid; }}
p {{ margin: 0 0 3mm; }}
ul, ol {{ margin: 0 0 3mm; padding-left: 6mm; }}
li {{ margin: 0 0 1.5mm; }}
code {{ font: 9pt Consolas, 'Courier New', monospace; background: #f3f4f6; border: 1px solid #e5e7eb;
  border-radius: 3px; padding: 0 1.2mm; }}
pre {{ background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 6px; padding: 3mm 4mm; font: 9pt/1.5 Consolas,
  'Courier New', monospace; white-space: pre-wrap; page-break-inside: avoid; margin: 0 0 4mm; }}
pre code {{ background: none; border: 0; padding: 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 0 0 4mm; font-size: 9.5pt; }}
th {{ background: #{GREEN}; color: #fff; text-align: left; padding: 2mm 2.5mm; }}
td {{ padding: 2mm 2.5mm; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
tr {{ page-break-inside: avoid; }}
tbody tr:nth-child(even) td {{ background: #f9fafb; }}
"""


def html_inline(text):
    out = []
    for kind, t in inline(text):
        t = html.escape(t)
        out.append({'bold': f'<strong>{t}</strong>', 'italic': f'<em>{t}</em>', 'code': f'<code>{t}</code>'}
                   .get(kind, t))
    return ''.join(out)


def data_uri(path):
    mime = 'image/png' if path.suffix == '.png' else 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()


def build_html(blocks):
    sections = [b[1] for b in blocks if b[0] == 'h1' and b[1] != 'Contents']
    body = []
    for kind, data in blocks:
        if kind == 'h1':
            if data == 'Contents':
                continue
            m = re.match(r'(\d+)\.\s*(.*)', data)
            num, title = (m.group(1).zfill(2), m.group(2)) if m else ('', data)
            body.append(f'<h1><small>{num}</small>{html.escape(title)}</h1>')
        elif kind == 'h2':
            body.append(f'<h2>{html_inline(data)}</h2>')
        elif kind == 'p':
            body.append(f'<p>{html_inline(data)}</p>')
        elif kind == 'code':
            body.append(f'<pre><code>{html.escape(data)}</code></pre>')
        elif kind == 'table':
            head, rows = data[0], data[1:]
            body.append('<table><thead><tr>' + ''.join(f'<th>{html_inline(c)}</th>' for c in head) + '</tr></thead><tbody>'
                        + ''.join('<tr>' + ''.join(f'<td>{html_inline(c)}</td>' for c in r) + '</tr>' for r in rows)
                        + '</tbody></table>')
        elif kind == 'list':
            body.append(html_list(data))
    toc = ''.join(f'<li>{html.escape(s)}</li>' for s in sections)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>eBilling MoMo Staff Manual</title>
<style>{CSS}</style></head><body>
<div class="cover">
  <img class="logo" src="{data_uri(LOGO)}" alt="IBS">
  <div class="mid">
    <img class="icon" src="{data_uri(APP_ICON)}" alt="">
    <div class="product">{PRODUCT}</div>
    <h1>{TITLE}</h1>
    <div class="sub">{html.escape(SUBTITLE)}</div>
    <div class="chip">Dashboard address: <code style="background:none;border:0;color:#fff">{ADDRESS}</code></div>
  </div>
  <div class="foot">{VERSION}<br>{RIGHTS}</div>
</div>
<div class="toc"><h2>Contents</h2><ol>{toc}</ol></div>
{''.join(body)}
</body></html>"""


def html_list(items):
    """Nested list from (depth, number-or-None, text) items."""
    out, stack = [], []
    for depth, num, text in items:
        tag = 'ol' if num else 'ul'
        while len(stack) > depth + 1:
            out.append(f'</li></{stack.pop()}>')
        if len(stack) == depth + 1:
            out.append('</li>')
        while len(stack) < depth + 1:
            out.append(f'<{tag}>')
            stack.append(tag)
        out.append(f'<li>{html_inline(text)}')
    while stack:
        out.append(f'</li></{stack.pop()}>')
    return ''.join(out)


def find_browser():
    candidates = [
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        shutil.which('google-chrome'), shutil.which('chromium'), shutil.which('msedge'),
    ]
    return next((c for c in candidates if c and Path(c).exists()), None)


def build_pdf(blocks, target):
    browser = find_browser()
    if not browser:
        print('No Edge or Chrome found: PDF not built.', file=sys.stderr)
        return False
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / 'manual.html'
        page.write_text(build_html(blocks), encoding='utf-8')
        subprocess.run([browser, '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
                        f'--print-to-pdf={target}', page.as_uri()], check=True, capture_output=True, timeout=120)
    return True


# ---------------------------------------------------------------- Word

def shade(cell_or_par, colour):
    props = cell_or_par._tc.get_or_add_tcPr() if hasattr(cell_or_par, '_tc') else cell_or_par._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), colour)
    props.append(shd)


def add_runs(paragraph, text, size=None, colour=None, bold=False):
    for kind, t in inline(text):
        run = paragraph.add_run(t)
        run.bold = bold or kind == 'bold'
        run.italic = kind == 'italic'
        if kind == 'code':
            run.font.name = 'Consolas'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Consolas')
            run.font.size = Pt((size or 10.5) - 1)
            run.font.color.rgb = RGBColor.from_string(GREEN_DARK)
        else:
            if size:
                run.font.size = Pt(size)
            if colour:
                run.font.color.rgb = RGBColor.from_string(colour)


def page_number(paragraph):
    run = paragraph.add_run()
    for kind, text in (('begin', None), (None, 'PAGE'), ('end', None)):
        if kind:
            el = OxmlElement('w:fldChar')
            el.set(qn('w:fldCharType'), kind)
        else:
            el = OxmlElement('w:instrText')
            el.set(qn('xml:space'), 'preserve')
            el.text = text
        run._r.append(el)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)


def rounded_icon():
    """The eBilling app icon with rounded corners (the JPEG has square, off-white ones)."""
    icon = Image.open(APP_ICON).convert('RGBA')
    mask = Image.new('L', icon.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *icon.size), radius=icon.width // 5, fill=255)
    icon.putalpha(mask)
    buf = io.BytesIO()
    icon.save(buf, 'PNG')
    buf.seek(0)
    return buf


def build_docx(blocks, target):
    doc = Document()
    normal = doc.styles['Normal']
    normal.font.name = 'Segoe UI'
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), 'Segoe UI')
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    for name, size in (('Heading 1', 18), ('Heading 2', 12.5)):
        style = doc.styles[name]
        style.font.name = 'Segoe UI'
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(GREEN_DARK if name == 'Heading 1' else '1F2937')
        style.paragraph_format.space_before = Pt(18 if name == 'Heading 1' else 12)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.keep_with_next = True

    # --- cover: one full-bleed green cell on a page with no margins
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(0)
    table = doc.add_table(rows=1, cols=1)
    table.autofit = False
    row = table.rows[0]
    row.height, row.height_rule = Cm(29.5), WD_ROW_HEIGHT_RULE.EXACTLY
    cell = row.cells[0]
    cell.width = Cm(21)
    shade(cell, COVER)
    # fixed full-page width (A4 = 11906 twips) so the green reaches both edges
    tblpr = table._tbl.tblPr
    for tag in ('w:tblW', 'w:tblLayout'):
        for old in tblpr.findall(qn(tag)):
            tblpr.remove(old)
    width = OxmlElement('w:tblW')
    width.set(qn('w:w'), '11906')
    width.set(qn('w:type'), 'dxa')
    tblpr.append(width)
    layout = OxmlElement('w:tblLayout')
    layout.set(qn('w:type'), 'fixed')
    tblpr.append(layout)
    table.columns[0].width = Cm(21)
    first = cell.paragraphs[0]
    first.paragraph_format.left_indent = Cm(2)
    first.paragraph_format.space_before = Pt(60)
    first.add_run().add_picture(str(LOGO), height=Cm(1.4))

    def cover_line(text, size, bold=False, before=0, after=6):
        p = cell.add_paragraph()
        p.paragraph_format.left_indent = Cm(2)
        p.paragraph_format.right_indent = Cm(2)
        p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(before), Pt(after)
        run = p.add_run(text)
        run.font.size, run.bold = Pt(size), bold
        run.font.color.rgb = RGBColor(255, 255, 255)
        return p

    cover_line('', 10, before=150)
    icon_par = cell.add_paragraph()
    icon_par.paragraph_format.left_indent = Cm(2)
    icon_par.add_run().add_picture(rounded_icon(), height=Cm(2.4))
    cover_line(PRODUCT, 13, bold=True, before=14, after=2)
    cover_line(TITLE, 34, bold=True, after=8)
    cover_line(SUBTITLE, 12, after=10)
    cover_line(f'Dashboard address: {ADDRESS}', 9.5, after=0)
    cover_line('', 10, before=110)
    cover_line(VERSION, 10.5, after=0)
    cover_line(RIGHTS, 10.5, after=0)

    # --- body: normal margins, page numbers in the footer
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.left_margin = body.right_margin = Cm(2)
    body.top_margin, body.bottom_margin = Cm(2), Cm(2)
    footer = body.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0]
    fp.alignment = 1
    page_number(fp)

    # --- contents
    heading = doc.add_paragraph()
    run = heading.add_run('Contents')
    run.bold, run.font.size = True, Pt(20)
    run.font.color.rgb = RGBColor.from_string(GREEN_DARK)
    for _, title in [b for b in blocks if b[0] == 'h1' and b[1] != 'Contents']:
        p = doc.add_paragraph(title)
        p.paragraph_format.space_after = Pt(4)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    for kind, data in blocks:
        if kind == 'h1' and data != 'Contents':
            doc.add_heading(data, level=1)
        elif kind == 'h2':
            doc.add_heading(strip_inline(data), level=2)
        elif kind == 'p':
            add_runs(doc.add_paragraph(), data)
        elif kind == 'code':
            for line in data.split('\n'):
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.left_indent = Cm(0.3)
                shade(p, 'F3F4F6')
                run = p.add_run(line or ' ')
                run.font.name = 'Consolas'
                run.font.size = Pt(9)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == 'list':
            for depth, num, text in data:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.9 + 0.8 * depth)
                p.paragraph_format.first_line_indent = Cm(-0.6)
                p.paragraph_format.space_after = Pt(3)
                p.add_run(f'{num}.\t' if num else ('•\t' if depth == 0 else '–\t'))
                p.paragraph_format.tab_stops.add_tab_stop(Cm(0.9 + 0.8 * depth))
                add_runs(p, text)
        elif kind == 'table':
            head, rows = data[0], data[1:]
            tbl = doc.add_table(rows=1, cols=len(head))
            tbl.style = 'Table Grid'
            tbl.autofit = True
            for i, text in enumerate(head):
                c = tbl.rows[0].cells[i]
                shade(c, GREEN)
                c.paragraphs[0].paragraph_format.space_after = Pt(2)
                add_runs(c.paragraphs[0], text, size=9.5, colour='FFFFFF', bold=True)
            for r in rows:
                cells = tbl.add_row().cells
                for i, text in enumerate(r[:len(head)]):
                    cells[i].paragraphs[0].paragraph_format.space_after = Pt(2)
                    add_runs(cells[i].paragraphs[0], text, size=9.5)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)

    doc.core_properties.title = f'{PRODUCT} {TITLE}'
    doc.core_properties.author = 'Intelligent Building Solutions'
    doc.save(target)


if __name__ == '__main__':
    parsed = parse(SOURCE.read_text(encoding='utf-8'))
    build_docx(parsed, DOCS / 'eBilling_MoMo_Staff_Manual.docx')
    print('Wrote eBilling_MoMo_Staff_Manual.docx')
    if build_pdf(parsed, DOCS / 'eBilling_MoMo_Staff_Manual.pdf'):
        print('Wrote eBilling_MoMo_Staff_Manual.pdf')

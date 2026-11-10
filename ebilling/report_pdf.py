"""Branded PDF export of the Reports page (ReportLab)."""
import io
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from PIL import Image
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

from .templatetags.dashboard_extras import CURRENCY

LOGO_PATH = Path(settings.BASE_DIR) / 'docs' / 'reference' / 'ibsgh_1.png'

BRAND = colors.HexColor('#017a3d')
BRAND_DARK = colors.HexColor('#015c2e')
BRAND_SOFT = colors.HexColor('#e8f5ee')
INK = colors.HexColor('#1f2937')
MUTED = colors.HexColor('#6b7280')
LINE = colors.HexColor('#e5e7eb')
ROSE = colors.HexColor('#be123c')
ZEBRA = colors.HexColor('#f9fafb')

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm


def _num(value):
    return f"{float(value or 0):,.2f}"


def _money(value):
    return f"{CURRENCY} {float(value or 0):,.2f}".strip()


@lru_cache(maxsize=1)
def _logo_images():
    """(solid logo, faded watermark) as in-memory PNGs, or (None, None) if the file is missing."""
    if not LOGO_PATH.exists():
        return None, None
    logo = Image.open(LOGO_PATH).convert('RGBA')
    faded = logo.copy()
    faded.putalpha(faded.getchannel('A').point(lambda a: int(a * 0.09)))
    out = []
    for im in (logo, faded):
        buf = io.BytesIO()
        im.save(buf, 'PNG')
        out.append(buf.getvalue())
    return tuple(out)


def _styles():
    base = getSampleStyleSheet()['Normal']
    mk = lambda name, **kw: ParagraphStyle(name, parent=base, fontName=kw.pop('fontName', 'Helvetica'), **kw)
    return {
        'title': mk('title', fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=INK),
        'sub': mk('sub', fontSize=9.5, leading=13, textColor=MUTED),
        'h2': mk('h2', fontName='Helvetica-Bold', fontSize=12, leading=16, textColor=BRAND_DARK,
                 spaceBefore=14, spaceAfter=6),
        'cell': mk('cell', fontSize=8.5, leading=11, textColor=INK),
        'right': mk('right', fontSize=8.5, leading=11, textColor=INK, alignment=TA_RIGHT),
        'label': mk('label', fontSize=8, leading=10, textColor=MUTED),
        'value': mk('value', fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=INK),
        'small': mk('small', fontSize=7.5, leading=10, textColor=MUTED),
    }


def _decorate(meta):
    """Page furniture: header band, centred watermark, footer."""
    logo, faded = _logo_images()

    def draw(canvas, doc):
        canvas.saveState()
        if faded:
            size = 120 * mm
            w, h = ImageReader(io.BytesIO(faded)).getSize()
            canvas.drawImage(ImageReader(io.BytesIO(faded)), (PAGE_W - size) / 2,
                             (PAGE_H - size * h / w) / 2, width=size, height=size * h / w, mask='auto')
        canvas.setFillColor(BRAND)
        canvas.rect(0, PAGE_H - 4 * mm, PAGE_W, 4 * mm, stroke=0, fill=1)
        if logo and doc.page == 1:
            w, h = ImageReader(io.BytesIO(logo)).getSize()
            lh = 20 * mm
            canvas.drawImage(ImageReader(io.BytesIO(logo)), MARGIN, PAGE_H - 10 * mm - lh,
                             width=lh * w / h, height=lh, mask='auto')
        elif logo:
            w, h = ImageReader(io.BytesIO(logo)).getSize()
            lh = 9 * mm
            canvas.drawImage(ImageReader(io.BytesIO(logo)), MARGIN, PAGE_H - 7 * mm - lh,
                             width=lh * w / h, height=lh, mask='auto')
            canvas.setFont('Helvetica', 8)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 13 * mm, f"{meta['label']} · {meta['app']}")
        canvas.setStrokeColor(LINE)
        canvas.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 9.5 * mm, f"Generated {meta['generated']:%d %b %Y, %H:%M} by {meta['user']}")
        canvas.drawRightString(PAGE_W - MARGIN, 9.5 * mm, f'Page {doc.page}')
        canvas.restoreState()

    return draw


def _table(header, body, footer, widths, st, first_left=True):
    """Report table: green header, zebra rows, bold total row."""
    data = [[Paragraph(f'<font color="white" size="7.5"><b>{h}</b></font>',
                       st['cell'] if i == 0 else st['right']) for i, h in enumerate(header)]]
    for row in body:
        data.append([Paragraph(str(c), st['cell'] if i == 0 else st['right']) for i, c in enumerate(row)])
    if footer:
        data.append([Paragraph(f'<b>{c}</b>', st['cell'] if i == 0 else st['right'])
                     for i, c in enumerate(footer)])
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), BRAND),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINE),
        ('ROWBACKGROUNDS', (0, 1), (-1, len(body)), [colors.white, ZEBRA]),
    ]
    if footer:
        style += [('BACKGROUND', (0, -1), (-1, -1), BRAND_SOFT), ('LINEABOVE', (0, -1), (-1, -1), 0.8, BRAND)]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(style))
    return t


def _chart(rows, unit, width):
    """Bar chart of amount collected per day/month."""
    drawing = Drawing(width, 150)
    values = [float(r['collected']) for r in rows]
    if not any(values):
        drawing.add(String(width / 2, 70, 'No collections in this period', textAnchor='middle',
                           fontName='Helvetica', fontSize=9, fillColor=MUTED))
        return drawing
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 44, 26, width - 56, 108
    chart.data = [values]
    chart.bars[0].fillColor = BRAND
    chart.bars[0].strokeColor = None
    chart.barWidth = 6
    chart.groupSpacing = 2
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fillColor = MUTED
    chart.valueAxis.labelTextFormat = lambda v: f'{v:,.0f}'
    chart.valueAxis.strokeColor = LINE
    chart.valueAxis.gridStrokeColor = LINE
    chart.valueAxis.visibleGrid = True
    chart.categoryAxis.strokeColor = LINE
    chart.categoryAxis.labels.fontName = 'Helvetica'
    chart.categoryAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.fillColor = MUTED
    step = max(1, len(rows) // 16)
    names = [(r['date'].strftime('%d' if unit == 'day' else '%b %y') if i % step == 0 else '')
             for i, r in enumerate(rows)]
    chart.categoryAxis.categoryNames = names
    drawing.add(chart)
    return drawing


def build_report_pdf(ctx):
    """Render the report context (as built by dashboard.reports) to PDF bytes."""
    st = _styles()
    totals, prev, deltas = ctx['totals'], ctx['prev_totals'], ctx['deltas']
    app = ctx['app'] or 'All applications'
    pct = ctx['fee_percent']
    meta = {'label': ctx['label'], 'app': app, 'generated': ctx['generated'], 'user': ctx['username']}

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=36 * mm,
                          bottomMargin=20 * mm, title=f"MoMo report – {ctx['label']}",
                          author='Intelligent Building Solutions')
    frame = Frame(MARGIN, 20 * mm, PAGE_W - 2 * MARGIN, PAGE_H - 56 * mm, id='f', leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='p', frames=[frame], onPage=_decorate(meta))])
    width = PAGE_W - 2 * MARGIN

    def delta_text(d):
        if d is None:
            return 'no previous data'
        return f'{"+" if d >= 0 else "-"}{abs(d)}% vs previous period'

    story = [
        Paragraph('Mobile Money Collections Report', st['title']),
        Paragraph(f"{ctx['label']} &nbsp;·&nbsp; {ctx['start']:%d %b %Y} – {ctx['end']:%d %b %Y} "
                  f"&nbsp;·&nbsp; {app}", st['sub']),
        Spacer(1, 10),
    ]

    def stat(label, value, sub):
        return [Paragraph(label.upper(), st['label']), Paragraph(value, st['value']),
                Paragraph(sub, st['small'])]

    rate = f"{totals['success_rate']}%" if totals['success_rate'] is not None else '—'
    cards = Table([[
        stat('Collected', _money(totals['collected']), delta_text(deltas['collected'])),
        stat('Requests', str(totals['count']), delta_text(deltas['count'])),
        stat('Success rate', rate,
             f"{totals['successful']} ok · {totals['failed']} failed · {totals['pending']} pending"),
        stat('Average payment', _money(totals['average']), 'per successful payment'),
    ]], colWidths=[width / 4] * 4)
    cards.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, LINE), ('INNERGRID', (0, 0), (-1, -1), 0.6, LINE),
        ('BACKGROUND', (0, 0), (-1, -1), colors.white), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEABOVE', (0, 0), (-1, 0), 2, BRAND),
    ]))
    story += [cards,
              Paragraph(f"Compared with {ctx['prev_start']:%d %b %Y} – {ctx['prev_end']:%d %b %Y}: "
                        f"{_money(prev['collected'])} collected from {prev['count']} requests.", st['small'])]

    summary = Table([
        ['Gross collected (successful payments)', _money(totals['collected'])],
        [f'Less deduction ({pct}%)', f"- {_money(totals['fee'])}"],
        ['Net amount', _money(totals['net'])],
    ], colWidths=[width * 0.7, width * 0.3], hAlign='LEFT')
    summary.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TEXTCOLOR', (0, 0), (-1, -1), INK), ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'), ('BACKGROUND', (0, 2), (-1, 2), BRAND_SOFT),
        ('LINEABOVE', (0, 2), (-1, 2), 0.8, BRAND), ('LINEBELOW', (0, 0), (-1, 1), 0.4, LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BOX', (0, 0), (-1, -1), 0.6, LINE),
    ]))
    story += [Spacer(1, 10), summary]

    unit = ctx['unit']
    story.append(KeepTogether([
        Paragraph(f"{'Daily' if unit == 'day' else 'Monthly'} collections", st['h2']),
        _chart(ctx['rows'], unit, width),
    ]))

    def row_cells(r, first):
        rate = f"{r['success_rate']}" if r['success_rate'] is not None else '—'
        cells = [first, r['count'], r['successful'], r['failed'], r['pending'], rate,
                 _num(r['collected']), _num(r['fee']), _num(r['net'])]
        if not r['count']:
            cells = [f'<font color="#9ca3af">{c}</font>' for c in cells]
        return cells

    cur = f' ({CURRENCY})' if CURRENCY else ''
    header = ['Requests', 'Success', 'Failed', 'Pending', 'Rate (%)', f'Collected{cur}',
              f'Deduction {pct}%', 'Net']
    widths = [width * w for w in (0.16, 0.10, 0.09, 0.08, 0.10, 0.08, 0.13, 0.13, 0.13)]
    story += [
        Paragraph(f'Breakdown by {unit}', st['h2']),
        _table([unit.capitalize()] + header,
               [row_cells(r, r['label']) for r in ctx['rows']],
               row_cells(totals, 'Total'), widths, st),
        Paragraph('By application', st['h2']),
    ]
    if ctx['by_app']:
        story.append(_table(['Application'] + header,
                            [row_cells(r, r['app'] or '—') for r in ctx['by_app']], None, widths, st))
    else:
        story.append(Paragraph('No transactions in this period.', st['sub']))

    doc.build(story)
    return buf.getvalue()

import json
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape as _xe

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FD = '/usr/share/fonts/truetype/dejavu/'
pdfmetrics.registerFont(TTFont('DejaVu', _FD + 'DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DejaVu-Bold', _FD + 'DejaVuSans-Bold.ttf'))
pdfmetrics.registerFontFamily('DejaVu', normal='DejaVu', bold='DejaVu-Bold')


def parse_details(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _yn(b) -> str:
    return 'так' if b else 'ні'


def _grid(data, widths):
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'DejaVu'),
        ('FONTNAME', (0, 0), (0, -1), 'DejaVu-Bold'),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f4f4f5')),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return t


def _mm_label(name: str) -> str:
    p = str(name).split('_', 2)
    return f"{p[0]} ({p[1]}) {p[2]}" if len(p) == 3 else str(name)


def generate_pdf_report(result_data: dict) -> BytesIO:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    st = getSampleStyleSheet()
    title = ParagraphStyle('T', parent=st['Heading1'], fontName='DejaVu-Bold',
                           fontSize=18, spaceAfter=30, alignment=1)
    head = ParagraphStyle('H', parent=st['Heading2'], fontName='DejaVu-Bold', fontSize=14,
                          textColor=colors.HexColor('#2563eb'), spaceAfter=12, spaceBefore=12)
    norm = ParagraphStyle('N', parent=st['Normal'], fontName='DejaVu', fontSize=11, leading=15)

    test_type = result_data.get('test_type', '')
    test_name = result_data.get('test_name') or test_type
    done = result_data.get('completed_at')
    if not isinstance(done, datetime):
        done = datetime.now()
    d = parse_details(result_data.get('details'))
    cl = d.get('clusters')

    story = [Paragraph('ЗВІТ ПРО ТЕСТУВАННЯ', title), Paragraph(_xe(test_name), head),
             Spacer(1, 0.5*cm),
             _grid([['Пацієнт:', str(result_data.get('patient_name') or '—')],
                    ['Дата:', done.strftime('%d.%m.%Y %H:%M')]] + ([] if test_type in ('minmult', 'schmishek') else [['Загальний бал:', str(result_data.get('score', '—'))]]) + ([['Стать:', {'M': 'Чоловіча', 'F': 'Жіноча'}.get(d.get('sex'), '—')]] if test_type == 'minmult' and d.get('sex') in ('M', 'F') else []), [5*cm, 10*cm]),
             Spacer(1, 1*cm)]

    if test_type == 'pcl5' and cl:
        story += [Paragraph('КЛАСТЕРИ', head),
                  _grid([['B (вторгнення)', f"{cl['B']} з 20"], ['C (уникнення)', f"{cl['C']} з 8"],
                         ['D (когніції/настрій)', f"{cl['D']} з 28"],
                         ['E (збудження)', f"{cl['E']} з 24"]], [7*cm, 8*cm]),
                  Spacer(1, 0.5*cm)]
        text = (f"Сума: {result_data.get('score')} з 80. Орієнтовний поріг ймовірного ПТСР (31–33) "
                f"досягнуто: {_yn(d.get('cutoff_met'))}. Попередній критерій DSM-5 "
                f"(≥1 B, ≥1 C, ≥2 D, ≥2 E при оцінці ≥2): {_yn(d.get('dsm_provisional'))}. "
                "PCL-5 є скринінговим інструментом: діагноз встановлює лише клініцист.")
    elif test_type == 'pcl5':
        text = 'Дані кластерів відсутні (результат отримано до оновлення скорера).'
    elif test_type == 'schmishek' and d.get('profile'):
        lvl = {'accentuation': 'акцентуація', 'tendency': 'тенденція', 'normal': 'норма'}
        story += [Paragraph('ПРОФІЛЬ АКЦЕНТУАЦІЙ', head),
                  _grid([['Шкала', 'Бал (макс. 24)', 'Рівень']] +
                        [[p['name'], str(p['score']), lvl.get(p['level'], '—')] for p in d['profile']],
                        [6*cm, 4.5*cm, 4.5*cm]),
                  Spacer(1, 0.5*cm)]
        text = ('Бал на кожній шкалі від 0 до 24. За джерелом: понад 19 балів — акцентуація, '
                '15–19 — лише тенденція; у літературі пороги розходяться. '
                'Акцентуація не є патологією. Профіль інтерпретує фахівець.')
    elif test_type == 'minmult' and d.get('subscales'):
        v = d.get('validity') or {}
        ts = d.get('t_scores') or {}
        tv = d.get('t_validity') or {}
        order = lambda kv: int(kv[0].split('_')[1]) if kv[0].count('_') >= 2 else 99
        rows = sorted(d['subscales'].items(), key=order)
        if ts:
            story += [Paragraph('ШКАЛИ (бали з поправкою на K) ТА T-БАЛИ', head),
                      _grid([['Шкала', 'Бал', 'T-бал']] +
                            [[_mm_label(n), str(x), str(ts.get(n, '—'))] for n, x in rows],
                            [7*cm, 4*cm, 4*cm]),
                      Spacer(1, 0.5*cm),
                      Paragraph('ОЦІНОЧНІ ШКАЛИ', head),
                      _grid([['Шкала', 'Бал', 'T-бал'],
                             ['L (брехня)', str(v.get('L', '—')), str(tv.get('L', '—'))],
                             ['F (вірогідність)', str(v.get('F', '—')), str(tv.get('F', '—'))],
                             ['K (корекція)', str(v.get('K', '—')), str(tv.get('K', '—'))]],
                            [7*cm, 4*cm, 4*cm]),
                      Spacer(1, 0.5*cm)]
            text = ('Бали шкал Hs, Pd, Pt, Sc, Ma подано з поправкою на K. '
                    'T-бали розраховано за формулою T = 50 + 10·(X − M)/σ за нормами джерела '
                    f"для статі: {'чоловіча' if d.get('sex') == 'M' else 'жіноча'}. "
                    'Профіль інтерпретує фахівець.')
        else:
            story += [Paragraph('ШКАЛИ (бали з поправкою на K)', head),
                      _grid([[_mm_label(n), str(x)] for n, x in rows], [9*cm, 6*cm]),
                      Spacer(1, 0.5*cm),
                      Paragraph('ОЦІНОЧНІ ШКАЛИ', head),
                      _grid([['L (брехня)', str(v.get('L', '—'))],
                             ['F (вірогідність)', str(v.get('F', '—'))],
                             ['K (корекція)', str(v.get('K', '—'))]], [9*cm, 6*cm]),
                      Spacer(1, 0.5*cm)]
            text = ('Бали шкал Hs, Pd, Pt, Sc, Ma подано з поправкою на K. '
                    'T-бали не розраховано (стать не вказана). Профіль інтерпретує фахівець.')
    else:
        text = 'Інтерпретація потребує фахівця.'

    story += [Paragraph('ІНТЕРПРЕТАЦІЯ', head), Paragraph(_xe(text), norm), Spacer(1, 1*cm),
              Paragraph('ЗАСТЕРЕЖЕННЯ', head),
              Paragraph('Звіт має інформаційний характер і не є медичним діагнозом. '
                        'Для інтерпретації результатів зверніться до кваліфікованого фахівця.', norm)]
    doc.build(story)
    buf.seek(0)
    return buf

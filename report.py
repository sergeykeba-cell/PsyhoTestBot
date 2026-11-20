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


SCREEN_MAX = {'phq9': 27, 'gad7': 21}
# Пороги (PHQ-9: 5/10/15/20; GAD-7: 5/10/15) НЕ звірено з первинними джерелами (Kroenke 2001; Spitzer 2006).
_SEV_UA = {
    'phq9': {'minimal': 'мінімальні або відсутні симптоми', 'mild': 'легкий рівень симптомів',
             'moderate': 'помірний рівень симптомів', 'moderately_severe': 'помірно тяжкий рівень симптомів',
             'severe': 'тяжкий рівень симптомів'},
    'gad7': {'minimal': 'мінімальна тривога', 'mild': 'легка тривога',
             'moderate': 'помірна тривога', 'severe': 'тяжка тривога'},
}
_I9_YES = 'ПУНКТ 9 PHQ-9 > 0 (думки про смерть або самопошкодження): потрібна негайна клінічна оцінка ризику.'
_I9_UNK = 'Прапорець пункту 9 PHQ-9 не визначено — перевірте відповіді пацієнта вручну.'


def screen_severity(test_type, score):
    try:
        s = int(score)
    except (TypeError, ValueError):
        return None
    bands = {'phq9': ((4, 'minimal'), (9, 'mild'), (14, 'moderate'),
                      (19, 'moderately_severe'), (27, 'severe')),
             'gad7': ((4, 'minimal'), (9, 'mild'), (14, 'moderate'), (21, 'severe'))}.get(test_type)
    if not bands or s < 0:
        return None
    for hi, k in bands:
        if s <= hi:
            return k
    return None


def gad7_cutoff(score) -> str:
    try:
        return 'так' if int(score) >= 10 else 'ні'
    except (TypeError, ValueError):
        return '—'


def severity_ua(test_type, key, score=None) -> str:
    if key is None or key in ('', 'pending'):
        key = screen_severity(test_type, score)
        if key is None:
            return '—'
    return _SEV_UA.get(test_type, {}).get(str(key), str(key))


def item9_alert(src: dict, d: dict = None) -> tuple:
    """('yes'|'unknown'|'no', текст). Не PHQ-9 або пункт 9 = 0 -> ('no', '')."""
    if src.get('test_type') != 'phq9':
        return 'no', ''
    d = d or {}
    for v in (src.get('item9_positive'), d.get('item9_positive')):
        if v is not None:
            return ('yes', _I9_YES) if str(v).lower() in ('true', '1') else ('no', '')
    ans = d.get('answers') or src.get('answers')
    if isinstance(ans, str):
        try:
            ans = json.loads(ans)
        except ValueError:
            ans = None
    if isinstance(ans, list) and len(ans) >= 9:
        try:
            return ('yes', _I9_YES) if int(ans[8]) > 0 else ('no', '')
        except (TypeError, ValueError):
            pass
    return 'unknown', _I9_UNK


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
    elif test_type in SCREEN_MAX:
        i9, alert = item9_alert({**result_data, 'test_type': test_type}, d)
        if alert:
            story += [Paragraph(_xe(alert), ParagraphStyle('A', parent=norm, fontName='DejaVu-Bold', textColor=colors.HexColor('#b91c1c'))),
                      Spacer(1, 0.5*cm)]
        story += [_grid([['Рівень:', severity_ua(test_type, result_data.get('severity'), result_data.get('score'))]], [5*cm, 10*cm]),
                  Spacer(1, 0.5*cm)]
        text = (f"Сума: {result_data.get('score')} з {SCREEN_MAX[test_type]}. "
                + (f"Поріг ≥10 досягнуто: {gad7_cutoff(result_data.get('score'))}. " if test_type == 'gad7' else '')
                + 'Скринінговий результат не є діагнозом. Потрібна клінічна оцінка фахівця.')
    else:
        text = 'Інтерпретація потребує фахівця.'

    story += [Paragraph('ІНТЕРПРЕТАЦІЯ', head), Paragraph(_xe(text), norm), Spacer(1, 1*cm),
              Paragraph('ЗАСТЕРЕЖЕННЯ', head),
              Paragraph('Звіт має інформаційний характер і не є медичним діагнозом. '
                        'Для інтерпретації результатів зверніться до кваліфікованого фахівця.', norm)]
    doc.build(story)
    buf.seek(0)
    return buf


_SCREEN_Q = {
    'phq9': [
        'Втрата інтересу або задоволення від звичних справ',
        'Пригнічений настрій, туга або відчуття безнадії',
        'Проблеми зі сном (безсоння або надмірна сонливість)',
        'Втома або брак енергії',
        'Поганий апетит або переїдання',
        'Низька самооцінка, відчуття провини або невдачі',
        'Складнощі з концентрацією уваги',
        'Загальмованість або, навпаки, метушливість',
        'Думки про смерть або самопошкодження',
    ],
    'gad7': [
        'Нервозність, тривога або напруження',
        'Неможливість зупинити або контролювати занепокоєння',
        'Надмірне занепокоєння з різних приводів',
        'Труднощі з розслабленням',
        'Неспокійність, важко всидіти на місці',
        'Легка дратівливість або роздратування',
        'Відчуття страху, що може трапитися щось жахливе',
    ],
}
_FREQ_UA = ['Зовсім ні', 'Кілька днів', 'Більше половини днів', 'Майже щодня']


def item_lines(test_type, src: dict, d: dict = None) -> list:
    """Рядки '1. Питання — 2 (Більше половини днів)'. [] якщо відповідей немає або кількість не збігається."""
    qs = _SCREEN_Q.get(test_type)
    if not qs:
        return []
    d = d or {}
    ans = d.get('answers') or src.get('answers')
    if isinstance(ans, str):
        try:
            ans = json.loads(ans)
        except ValueError:
            return []
    if not isinstance(ans, list) or len(ans) != len(qs):
        return []
    out = []
    for i, (q, a) in enumerate(zip(qs, ans), 1):
        try:
            a = int(a)
        except (TypeError, ValueError):
            return []
        if not 0 <= a <= 3:
            return []
        flag = ' ⚠️' if (test_type == 'phq9' and i == 9 and a > 0) else ''
        out.append(f"{i}. {q} — {a} ({_FREQ_UA[a]}){flag}")
    return out

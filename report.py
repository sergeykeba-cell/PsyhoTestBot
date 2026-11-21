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


SCREEN_MAX = {'phq9': 27, 'gad7': 21, 'isi': 28, 'phq15': 30, 'pss10': 40, 'who5': 100, 'nsi': 88, 'audit_c': 12, 'audit': 40}
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
    if src.get('test_type') not in ('phq9', 'express'):
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
                    ['Дата:', done.strftime('%d.%m.%Y %H:%M')]] + ([['Стать:', {'M': 'Чоловіча', 'F': 'Жіноча'}[(d.get('sex') or d.get('sex_used'))]]] if test_type != 'minmult' and (d.get('sex') or d.get('sex_used')) in ('M', 'F') else []) + ([] if test_type in ('minmult', 'schmishek', 'brief_cope', 'c_ssrs', 'express') else [['Загальний бал:', str(result_data.get('score', '—'))]]) + ([['Стать:', {'M': 'Чоловіча', 'F': 'Жіноча'}.get(d.get('sex'), '—')]] if test_type == 'minmult' and d.get('sex') in ('M', 'F') else []), [5*cm, 10*cm]),
             Spacer(1, 1*cm)]

    if test_type == 'pcl5' and cl:
        story += [Paragraph('КЛАСТЕРИ', head),
                  _grid([['B (вторгнення)', f"{cl['B']} / 20"], ['C (уникнення)', f"{cl['C']} / 8"],
                         ['D (когніції/настрій)', f"{cl['D']} / 28"],
                         ['E (збудження)', f"{cl['E']} / 24"]], [7*cm, 8*cm]),
                  Spacer(1, 0.5*cm)]
        text = (f"Сума: {result_data.get('score')} / 80. Орієнтовний поріг ймовірного ПТСР (31–33) "
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
        text = (f"Сума: {result_data.get('score')} / {SCREEN_MAX[test_type]}. "
                + (f"Поріг ≥10 досягнуто: {gad7_cutoff(result_data.get('score'))}. " if test_type == 'gad7' else '')
                + 'Скринінговий результат не є діагнозом. Потрібна клінічна оцінка фахівця.')
    else:
        text = 'Інтерпретація потребує фахівця.'
        if test_type == 'c_ssrs':
            story += [Paragraph(_xe('Суїцидальний ризик: ' + severity_ua(test_type, result_data.get('severity'), result_data.get('score'))),
                                ParagraphStyle('A', parent=norm, fontName='DejaVu-Bold', textColor=colors.HexColor('#b91c1c'))),
                      Spacer(1, 0.5*cm)]
            text = ('Скринінг не є діагнозом. Оцінку суїцидального ризику та подальші дії визначає фахівець. '
                    'Пункти 3–5 ставляться лише за відповіді «Так» на пункт 2.')
        if test_type == 'express':
            _sc = (d or {}).get('scales') or []
            if (d or {}).get('item9_positive'):
                story += [Paragraph(_xe('Увага: позитивна відповідь на пункт 9 PHQ-9 — потрібна пріоритетна оцінка суїцидального ризику.'),
                                    ParagraphStyle('A', parent=norm, fontName='DejaVu-Bold', textColor=colors.HexColor('#b91c1c'))),
                          Spacer(1, 0.5*cm)]
            if _sc:
                story += [Paragraph('ЗВЕДЕНИЙ ЗВІТ ПО ШКАЛАХ', head),
                          _grid([[x.get('name', ''), f"{x.get('score')} / {x.get('max')} — {x.get('level_ua')}"] for x in _sc], [6*cm, 9*cm]),
                          Spacer(1, 0.5*cm)]
            text = ('Комплексний скринінг об’єднує PHQ-9, GAD-7, ISI та AUDIT-C. Кожна шкала оцінюється окремо, '
                    'загального балу немає. Результат не є діагнозом, оцінку виконує фахівець.')
        _sub = d.get('subscales') if isinstance(d, dict) else None  # _SUBSCALES_PDF
        if _sub:
            story += [Paragraph('ПІДШКАЛИ', head),
                      _grid([[str(k).replace('_', ' '), str(v)] for k, v in sorted(_sub.items(), key=lambda kv: -kv[1])], [9*cm, 6*cm]),
                      Spacer(1, 0.5*cm)]
            _mac = d.get('macro')
            if _mac:
                story += [Paragraph('УМОВНЕ ГРУПУВАННЯ (середнє 0–3)', head),
                          _grid([[str(k), str(m.get('mean'))] for k, m in _mac.items()], [9*cm, 6*cm]),
                          Spacer(1, 0.5*cm)]
            if d.get('substance_flag'):
                story += [Paragraph(_xe('Увага: підшкала «Вживання речовин» ≥ 3 з 6.'), norm), Spacer(1, 0.3*cm)]
            text = ('Кожна підшкала — сума двох пунктів (0–6). Клінічних порогів немає: '
                    'профіль показує, які стратегії обираються частіше, і оцінюється фахівцем.')

    story += [Paragraph('ІНТЕРПРЕТАЦІЯ', head), Paragraph(_xe(text), norm), Spacer(1, 1*cm)]
    story += _items_story(test_type, item_rows(test_type, result_data, d), head, norm)
    story += [Paragraph('ЗАСТЕРЕЖЕННЯ', head),
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



def item_rows(test_type, src: dict, d: dict = None) -> list:
    # [(№, питання, бал)] або [], якщо відповідей немає чи їх кількість не збігається
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
        out.append((i, q, a))
    return out


def _items_story(test_type, rows, head, norm) -> list:
    if not rows:
        return []
    cell = ParagraphStyle('IC', parent=norm, fontSize=9.5, leading=12)
    bold = ParagraphStyle('IB', parent=cell, fontName='DejaVu-Bold')
    red = ParagraphStyle('IR', parent=bold, textColor=colors.HexColor('#b91c1c'))
    data = [[Paragraph('№', bold), Paragraph('Питання', bold),
             Paragraph('Відповідь', bold), Paragraph('Бал', bold)]]
    alert_rows = []
    for i, q, a in rows:
        flag = test_type == 'phq9' and i == 9 and a > 0
        stl = red if flag else cell
        data.append([Paragraph(str(i), stl), Paragraph(_xe(q), stl),
                     Paragraph(_xe(_FREQ_UA[a]), stl), Paragraph(str(a), stl)])
        if flag:
            alert_rows.append(len(data) - 1)
    data.append([Paragraph('', bold), Paragraph(_SUM_LABEL.get(test_type, 'Сума'), bold), Paragraph('', bold),
                 Paragraph(str(sum(a for _, _, a in rows)), bold)])
    t = Table(data, colWidths=[1*cm, 8*cm, 4.5*cm, 1.5*cm], repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f4f4f5')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f4f4f5')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for r in alert_rows:
        style.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#fee2e2')))
    t.setStyle(TableStyle(style))
    return [Paragraph('ВІДПОВІДІ ПО ПУНКТАХ', head), t, Spacer(1, 1*cm)]



# ---- загальний механізм: питання беруться з questions.json (витяг з index.html) ----
import os as _os

_QFILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'questions.json')
_QCACHE = {'mtime': None, 'data': {}}


def _questions_db() -> dict:
    try:
        m = _os.path.getmtime(_QFILE)
    except OSError:
        return {}
    if _QCACHE['mtime'] != m:
        try:
            with open(_QFILE, encoding='utf-8') as f:
                _QCACHE['data'] = json.load(f)
        except (OSError, ValueError):
            _QCACHE['data'] = {}
        _QCACHE['mtime'] = m
    return _QCACHE['data']


def _test_spec(test_type):
    spec = _questions_db().get(test_type)
    if spec and spec.get('questions'):
        opts = spec.get('options') or []
        return spec['questions'], opts, spec.get('values') or list(range(len(opts)))
    qs = _SCREEN_Q.get(test_type)
    if qs:
        return qs, _FREQ_UA, [0, 1, 2, 3]
    return None


def _is_scored(test_type) -> bool:
    spec = _test_spec(test_type)
    return bool(spec) and len(spec[2]) > 2


def item_rows(test_type, src: dict, d: dict = None) -> list:
    # [(№, питання, значення, підпис)] або [], якщо відповідей немає чи їх кількість не збігається
    spec = _test_spec(test_type)
    if not spec:
        return []
    qs, opts, vals = spec
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
        if a not in vals:
            return []
        k = vals.index(a)
        out.append((i, q, a, opts[k] if k < len(opts) else str(a)))
    return out


def item_lines(test_type, src: dict, d: dict = None) -> list:
    scored = _is_scored(test_type)
    out = []
    for i, q, a, label in item_rows(test_type, src, d):
        flag = ' ⚠️' if (test_type == 'phq9' and i == 9 and a > 0) else ''
        out.append(f"{i}. {q} — {f'{a} ({label})' if scored else label}{flag}")
    return out


def _items_story(test_type, rows, head, norm) -> list:
    if not rows:
        return []
    scored = _is_scored(test_type)
    cell = ParagraphStyle('IC', parent=norm, fontSize=9.5, leading=12)
    bold = ParagraphStyle('IB', parent=cell, fontName='DejaVu-Bold')
    red = ParagraphStyle('IR', parent=bold, textColor=colors.HexColor('#b91c1c'))
    hdr = [Paragraph('№', bold), Paragraph('Питання', bold), Paragraph('Відповідь', bold)]
    if scored:
        hdr.append(Paragraph('Бал', bold))
    data, alert_rows = [hdr], []
    for i, q, a, label in rows:
        flag = test_type == 'phq9' and i == 9 and a > 0
        stl = red if flag else cell
        row = [Paragraph(str(i), stl), Paragraph(_xe(q), stl), Paragraph(_xe(label), stl)]
        if scored:
            row.append(Paragraph(str(a), stl))
        data.append(row)
        if flag:
            alert_rows.append(len(data) - 1)
    if scored:
        data.append([Paragraph('', bold), Paragraph(_SUM_LABEL.get(test_type, 'Сума'), bold), Paragraph('', bold),
                     Paragraph(str(sum(r[2] for r in rows)), bold)])
        widths = [1*cm, 9*cm, 4.5*cm, 1.5*cm]
    else:
        widths = [1.2*cm, 11.8*cm, 3*cm]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f4f4f5')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if scored:
        style.append(('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f4f4f5')))
    for r in alert_rows:
        style.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#fee2e2')))
    t.setStyle(TableStyle(style))
    return [Paragraph('ВІДПОВІДІ ПО ПУНКТАХ', head), t, Spacer(1, 1*cm)]


# ---- етап 1: підписи рівнів для нових шкал ----
_SEV_UA_NEW = {
    'isi':   {'none': 'немає клінічно значущої безсоння', 'subthreshold': 'субпорогова безсоння',
              'moderate': 'клінічна безсоння помірної тяжкості', 'severe': 'клінічна безсоння тяжкого ступеня'},
    'phq15': {'minimal': 'мінімальна соматизація', 'low': 'низький рівень', 'medium': 'середній рівень',
              'high': 'високий рівень'},
    'pss10': {'low': 'низький рівень стресу', 'moderate': 'помірний рівень стресу (умовні межі)',
              'high': 'високий рівень стресу (умовні межі)'},
    'who5':  {'ok': 'благополуччя в межах норми', 'low': 'знижене благополуччя (≤50)',
              'very_low': 'дуже низьке благополуччя (≤28), рекомендовано скринінг депресії'},
    'nsi':   {'pending': 'передано лікарю'},
}
_orig_severity_ua = severity_ua


def severity_ua(test_type, severity=None, score=None):
    if test_type in _SEV_UA_NEW:
        return _SEV_UA_NEW[test_type].get(severity, str(severity or '—'))
    return _orig_severity_ua(test_type, severity, score)

_SEV_UA_NEW['brief_cope'] = {'pending': 'передано лікарю'}


# ---- виправлення 3 ----
_SUM_LABEL = {
    'who5':  'Сума сирих балів (індекс = ×4)',
    'pss10': 'Сума сирих балів (без обернення пунктів 4, 5, 7, 8)',
}
_orig_is_scored = _is_scored


def _is_scored(test_type) -> bool:
    if test_type == 'brief_cope':
        return False
    return _orig_is_scored(test_type)


# ---- етап 3а: AUDIT-C / AUDIT і варіанти відповідей на рівні питання ----
_SEV_UA_NEW['audit_c'] = {'negative': 'негативний скринінг (<3)', 'borderline': 'позитивний для жінок (≥3), для чоловіків нижче порога',
                          'positive': 'позитивний скринінг (≥4)'}
_SEV_UA_NEW['audit'] = {'low_risk': 'низький ризик', 'hazardous': 'ризиковане вживання', 'harmful': 'шкідливе вживання',
                        'dependence': 'імовірна залежність (потрібна оцінка фахівця)'}


def _has_qopt(test_type) -> bool:
    return bool((_questions_db().get(test_type) or {}).get('qoptions'))


def _qopt_answers(src, d):
    ans = (d or {}).get('answers') or (src or {}).get('answers')
    if isinstance(ans, str):
        try:
            ans = json.loads(ans)
        except ValueError:
            return None
    return ans


_orig_item_rows = item_rows


def item_rows(test_type, src, d=None):
    if not _has_qopt(test_type):
        return _orig_item_rows(test_type, src, d)
    spec = _questions_db()[test_type]
    qs, qo = spec['questions'], spec['qoptions']
    ans = _qopt_answers(src, d)
    if not isinstance(ans, list) or len(ans) != len(qs):
        return []
    rows = []
    for i, (q, a) in enumerate(zip(qs, ans)):
        o = qo[i]
        try:
            label = o['options'][o['values'].index(a)]
        except (ValueError, IndexError):
            label = str(a)
        rows.append((i + 1, q, a, label))
    return rows


_orig_item_lines = item_lines


def item_lines(test_type, src, d=None):
    if not _has_qopt(test_type):
        return _orig_item_lines(test_type, src, d)
    return [f"{i}. {q} — {a} ({label})" for i, q, a, label in item_rows(test_type, src, d)]


_orig_is_scored_3a = _is_scored


def _is_scored(test_type) -> bool:
    if test_type in ('audit_c', 'audit'):
        return True
    return _orig_is_scored_3a(test_type)


# ---- етап 3б: C-SSRS ----
_SEV_UA_NEW['c_ssrs'] = {'none': 'ознак ризику не виявлено', 'low': 'низький (пп. 1–2)',
                         'moderate': 'помірний (п. 3 або минула поведінка)',
                         'high': 'ВИСОКИЙ (наміри/план або поведінка за останні 3 міс.)'}
_orig_item_rows_3b = item_rows


def item_rows(test_type, src, d=None):
    rows = _orig_item_rows_3b(test_type, src, d)
    if test_type == 'c_ssrs' and len(rows) == 6 and rows[1][2] == 0:
        for k in (2, 3, 4):
            rows[k] = (rows[k][0], rows[k][1], 0, 'Не ставилося (п. 2 — «Ні»)')
    return rows


# ---- етап 4: комплексний експрес-скринінг ----
_SEV_UA_NEW['express'] = {'none': 'ознак не виявлено', 'attention': 'легкі прояви за однією зі шкал',
                          'elevated': 'є клінічно значущі шкали', 'high': 'виражені прояви за однією зі шкал'}
_orig_is_scored_4 = _is_scored


def _is_scored(test_type) -> bool:
    if test_type == 'express':
        return False
    return _orig_is_scored_4(test_type)


_orig_item9_alert = item9_alert


def item9_alert(res, d=None):
    if (res or {}).get('test_type') == 'express':
        dd = d if isinstance(d, dict) else {}
        pos = res.get('item9_positive')
        if pos is None:
            pos = dd.get('item9_positive')
        if pos is None:
            ans = _qopt_answers(res, dd)
            pos = isinstance(ans, list) and len(ans) > 8 and ans[8] > 0
        if pos:
            return ('yes', 'Позитивна відповідь на пункт 9 PHQ-9 (комплексний скринінг): потрібна пріоритетна оцінка суїцидального ризику.')
        return (None, None)
    return _orig_item9_alert(res, d)

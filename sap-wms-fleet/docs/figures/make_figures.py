"""生成接口总览、调用时序、确认校验、架构原理和运行状态五张图，输出 PDF、SVG 和 600 dpi PNG。

运行状态图读取同目录的 run_trace.json，由 record_run.py 在一次真实运行中采集。

用法：python make_figures.py [zh|en]，不带参数时两种语言都生成。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

OUT = Path(__file__).resolve().parent
LANG = 'zh'

SANS = ['WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'Microsoft YaHei', 'Arial']
MONO = ['WenQuanYi Micro Hei Mono', 'Noto Sans CJK SC', 'Consolas', 'Microsoft YaHei']
plt.rcParams.update({
    'font.family': SANS,
    'font.size': 6.5,
    'pdf.fonttype': 42,
    'svg.fonttype': 'none',
    'axes.unicode_minus': False,
})

# Okabe-Ito 色板，色弱读者也能区分
SAP = '#0072B2'
BRIDGE = '#E69F00'
WMS = '#009E73'
TCS = '#D55E00'
CAR = '#CC79A7'
INK = '#222222'
GREY = '#6B6B6B'
LINE = '#9A9A9A'
BAD = '#B2182B'


def T(zh, en):
    return zh if LANG == 'zh' else en


def tint(color, amount):
    color = color.lstrip('#')
    rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
    mixed = [round(c + (255 - c) * amount) for c in rgb]
    return '#' + ''.join(f'{c:02x}' for c in mixed)


def canvas(width, height):
    fig = plt.figure(figsize=(width / 25.4, height / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis('off')
    return fig, ax


def box(ax, x, y, w, h, fc='white', ec=INK, lw=0.6, ls='-', r=1.0, z=2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}',
                                fc=fc, ec=ec, lw=lw, ls=ls, zorder=z))


def arrow(ax, x1, y1, x2, y2, color=INK, dashed=False, lw=0.6, z=3, rad=0.0, head=5):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle='-|>', mutation_scale=head,
                                 color=color, lw=lw, ls=(0, (3, 2)) if dashed else '-',
                                 shrinkA=0, shrinkB=0, zorder=z,
                                 connectionstyle=f'arc3,rad={rad}'))


def text(ax, x, y, s, size=6.5, color=INK, ha='left', va='center', weight='normal', mono=False,
         bg=None, z=5, style='normal', rotation=0):
    kwargs = {}
    if bg:
        kwargs['bbox'] = dict(fc=bg, ec='none', pad=0.6)
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, fontweight=weight, zorder=z,
            family=MONO if mono else SANS, fontstyle=style, rotation=rotation,
            linespacing=1.25, **kwargs)


def panel(ax, x, y, letter):
    text(ax, x, y, letter, size=9, weight='bold', va='top')


def save(fig, name):
    for ext in ('pdf', 'svg', 'png'):
        fig.savefig(OUT / f'{name}_{LANG}.{ext}', dpi=600 if ext == 'png' else None)
    plt.close(fig)


# ---------------------------------------------------------------- 图 1

def system(ax, x, y, w, h, color, title, subtitle, lines=()):
    box(ax, x, y, w, h, fc=tint(color, 0.9), ec=color, lw=0.8)
    ax.add_patch(Rectangle((x + 0.4, y + 0.4), 1.4, h - 0.8, fc=color, ec='none', zorder=3))
    text(ax, x + 3.2, y + 3.2, title, size=7, weight='bold')
    text(ax, x + 3.2, y + 6.6, subtitle, size=5.8, color=GREY)
    for i, line in enumerate(lines):
        text(ax, x + 3.2, y + 10.6 + i * 3.4, line, size=6)


def figure_interfaces():
    W, H = 180, 211
    fig, ax = canvas(W, H)

    panel(ax, 1.5, 2, 'a')
    text(ax, 4, 5.2, T('一条串行链：SAP → WMS → Fleet Manager → 车辆。库位和托盘留在 WMS 内部，不另画成系统。',
                       'One serial chain: SAP → WMS → Fleet Manager → vehicle. Bins stay inside the WMS.'),
         size=6.2, weight='bold')

    def link(x1, y1, x2, y2, color, label, ly):
        arrow(ax, x1, y1, x2, y2, color=color)
        arrow(ax, x2, y2 + 2.2, x1, y1 + 2.2, color=GREY, dashed=True)
        text(ax, (x1 + x2) / 2, ly, label, size=5.6, ha='center', color=color, weight='bold', bg='white')

    system(ax, 4, 10, 38, 36, SAP, 'SAP ERP', 'OData V2 · :4004',
           [T('工厂 1000 / 库存地点 FG00', 'plant 1000 / sloc FG00'),
            T('没有仓位', 'no bin'),
            'PostGoodsReceipt · 101'])
    system(ax, 50, 10, 38, 36, WMS, 'WMS', T('库位和托盘在内部', 'bins and pallets inside'),
           [T('先调用 SAP', 'calls SAP first'),
            T('自己登记托盘、建上架单', 'registers pallet, opens putaway'),
            T('内部单就绪后才往下走', 'then calls the next system')])
    system(ax, 96, 10, 38, 36, TCS, 'Fleet Manager', T('本实验用 openTCS · :55200', 'openTCS in this lab · :55200'),
           [T('只接 WMS 的上架订单', 'accepts the WMS putaway'),
            'INB-1800000001',
            T('收货位装货 → 存储位卸货', 'load at GR, unload at storage')])
    system(ax, 142, 10, 34, 36, CAR, 'Vehicle-01', 'Loopback',
           [T('只向 Fleet Manager 回报', 'reports only to Fleet Manager'),
            'Point-GR → Point-Store'])
    link(42, 24, 50, 24, SAP, 'S1–S5', 22.2)
    text(ax, 46, 29.5, 'OData', size=5.2, ha='center', color=GREY)
    link(88, 24, 96, 24, TCS, 'F1–F6', 22.2)
    text(ax, 92, 29.5, 'REST', size=5.2, ha='center', color=GREY)
    link(134, 24, 142, 24, CAR, T('驱动', 'driver'), 22.2)

    text(ax, 4, 52, T('顺序：① WMS 读 SAP 交货  ② WMS 内部上架单  ③ WMS 调用 Fleet Manager  ④ 车辆执行  ⑤ WMS 向 SAP 过账收货',
                      'Order: ① WMS reads SAP  ② WMS putaway  ③ WMS calls Fleet Manager  ④ vehicle  ⑤ WMS posts goods receipt'),
         size=5.6, color=GREY)

    # ---- b 接口清单
    top = 88
    panel(ax, 1.5, top - 1, 'b')
    cols = [(4, T('编号', 'ID')), (12, T('方法', 'Method')), (24, T('路径', 'Path')),
            (113, T('作用', 'Purpose')), (157, T('阶段', 'Phase'))]
    y = top + 3
    for cx, name in cols:
        text(ax, cx, y, name, size=6.2, weight='bold')
    ax.plot([4, 176], [y + 2.2, y + 2.2], color=INK, lw=0.8)
    ax.plot([4, 176], [top, top], color=INK, lw=0.8)
    y += 2.2

    groups = [
        (SAP, T('SAP ERP · {INB} = /sap/opu/odata/sap/API_INBOUND_DELIVERY_SRV;v=0002',
                'SAP ERP · {INB} = /sap/opu/odata/sap/API_INBOUND_DELIVERY_SRV;v=0002'),
         T('{PROD} = API_PRODUCT_SRV，{OUT} = API_OUTBOUND_DELIVERY_SRV;v=0002；过账需 X-CSRF-Token 与 If-Match',
           '{PROD} = API_PRODUCT_SRV, {OUT} = API_OUTBOUND_DELIVERY_SRV;v=0002; posting needs X-CSRF-Token and If-Match'),
         [('S1', 'GET', '{INB}/$metadata', T('就绪探测，读元数据', 'readiness, metadata'), '①'),
          ('S2', 'GET', "{INB}/A_InbDeliveryHeader?$filter=…Status eq 'A'", T('轮询未过账入库交货', 'poll open inbound deliveries'), '②'),
          ('S3', 'GET', "{INB}/A_InbDeliveryHeader('1800000001')", T('读交货状态与 ETag', 'read status and ETag'), '⑥'),
          ('S4', 'HEAD', '{INB}/        X-CSRF-Token: Fetch', T('取 CSRF 令牌', 'fetch CSRF token'), '⑥'),
          ('S5', 'POST', "{INB}/PostGoodsReceipt?DeliveryDocument='1800000001'", T('过账收货，移动类型 101', 'post goods receipt, 101'), '⑥'),
          ('S6', 'GET', '{PROD}/A_Product    A_ProductStorageLocation', T('同步物料和库存地点', 'sync material and storage location'), '①'),
          ('S7', 'POST', "{OUT}/PostGoodsIssue?DeliveryDocument='8000000001'", T('发货过账，本链路不自动调用', 'goods issue, not automatic here'), T('出库', 'outbound'))]),
        (WMS, T('WMS 内部 · 库位和托盘，不是另一个系统 · OpenWMS Common :8120',
                'Inside the WMS · locations and pallets, not another system · OpenWMS Common :8120'), None,
         [('C1', 'GET', '/actuator/info', T('就绪探测', 'readiness'), '①'),
          ('C2', 'GET POST', '/v1/locations?locationId=…', T('查询 / 创建库位', 'find / create location'), '①'),
          ('C3', 'GET POST', '/v1/transport-units?bk=…[&actualLocation=…&tut=PALLET]', T('查询 / 创建托盘', 'find / create pallet'), '①'),
          ('C4', 'PATCH', '/v1/transport-units?bk=…&newLocation=…', T('移动托盘', 'move pallet'), '③ ⑥')]),
        (WMS, T('WMS 内部 · 上架运输单，不是另一个系统 · OpenWMS TMS :8131',
                'Inside the WMS · putaway orders, not another system · OpenWMS TMS :8131'), None,
         [('T1', 'GET', '/actuator/info', T('就绪探测', 'readiness'), '①'),
          ('T2', 'POST', '/v1/transport-orders', T('创建运输单，返回 201 + Location', 'create order, 201 + Location'), '③'),
          ('T3', 'GET', '/v1/transport-orders/{pKey}', T('读运输单状态', 'read order state'), '③ ⑥'),
          ('T4', 'POST', '/v1/transport-orders/{pKey}?state=STARTED|FINISHED|ONFAILURE|CANCELED', T('改运输单状态', 'change order state'), '③ ⑥')]),
        (TCS, T('Fleet Manager · 本实验用 openTCS · http://opentcs:55200',
                'Fleet Manager · openTCS in this lab · http://opentcs:55200'), None,
         [('F1', 'GET', '/v1/kernel/version', T('就绪探测', 'readiness'), '①'),
          ('F2', 'GET PUT', '/v1/plantModel', T('读取 / 上传工厂模型', 'read / upload plant model'), '①'),
          ('F3', 'PUT', '/v1/vehicles/Vehicle-01/commAdapter/…',
           T('挂 Loopback 驱动并允许接单', 'attach Loopback, allow orders'), '①'),
          ('F4', 'POST', '/v1/transportOrders/INB-{DeliveryDocument}', T('下上架订单：收货位装货 → 存储位卸货', 'putaway: load at GR, unload at storage'), '④'),
          ('F5', 'GET', '/v1/transportOrders/INB-{DeliveryDocument}', T('读订单状态', 'read order state'), '⑤'),
          ('F6', 'POST', '/v1/transportOrders/INB-{DeliveryDocument}/withdrawal?immediate=false', T('失败时撤回订单', 'withdraw on failure'), T('失败', 'failure'))]),
        (WMS, T('WMS · http://wms:8090', 'WMS · http://wms:8090'), None,
         [('W1', 'GET', '/status   /health', T('交货对照、上架进度、调用 SAP 的日志', 'delivery links, progress, SAP calls'), T('观测', 'observe'))]),
    ]
    row = 3.9
    for color, title, note, rows in groups:
        height = row * (2 if note else 1)
        ax.add_patch(Rectangle((4, y), 172, height, fc=tint(color, 0.88), ec='none', zorder=1))
        ax.add_patch(Rectangle((4, y), 1.0, height, fc=color, ec='none', zorder=2))
        text(ax, 6.4, y + row / 2, title, size=5.8, weight='bold', mono=title.startswith('SAP'))
        if note:
            text(ax, 6.4, y + row * 1.5, note, size=5.4, color=GREY, mono=True)
        y += height
        for i, (rid, method, path, purpose, phase) in enumerate(rows):
            text(ax, 4.6, y + row / 2, rid, size=6, weight='bold', color=color)
            text(ax, 12, y + row / 2, method, size=5.8, mono=True)
            text(ax, 24, y + row / 2, path, size=5.6, mono=True)
            text(ax, 113, y + row / 2, purpose, size=6)
            text(ax, 157, y + row / 2, phase, size=6)
            y += row
            ax.plot([4, 176], [y, y], color='#DDDDDD', lw=0.3, zorder=1)
    ax.plot([4, 176], [y, y], color=INK, lw=0.8)
    text(ax, 4, y + 3.4, T('阶段按调用顺序：① 初始化  ② WMS 查询 SAP  ③ WMS 内部上架单  ④ WMS 调用 Fleet Manager  ⑤ 车辆执行  ⑥ WMS 向 SAP 过账。'
                           '车辆只和 Fleet Manager 说话，没有单独的 HTTP 端点。',
                           'Phases follow the call order: ① init  ② WMS queries SAP  ③ putaway inside WMS  '
                           '④ WMS calls Fleet Manager  ⑤ vehicle  ⑥ WMS posts to SAP. '
                           'The vehicle talks only to Fleet Manager.'),
         size=5.6, color=GREY)
    save(fig, 'fig1_interfaces')


# ---------------------------------------------------------------- 图 2

def figure_sequence():
    W = 180
    lanes = {
        'sap': (32, SAP, 'SAP ERP', ':4004'),
        'wms': (78, WMS, 'WMS', T('内部含库位', 'bins inside')),
        'fleet': (124, TCS, 'Fleet Manager', ':55200'),
        'car': (164, CAR, 'Vehicle-01', 'Loopback'),
    }
    X = {k: v[0] for k, v in lanes.items()}

    # (类型, 参数...)；msg: 起点, 终点, 主标签, 细节, 是否响应
    steps = [
        ('phase', '①', T('初始化', 'init'), T('启动时', 'startup')),
        ('ref', T('ref  初始化：WMS 同步 SAP 主数据并准备自己的库位；Fleet Manager 上传路线并启用车辆。托盘等交货来了再在 WMS 内部登记',
                  'ref  startup: WMS syncs SAP master data and prepares its bins; Fleet Manager loads the route and enables the vehicle')),
        ('phase', '②', T('读交货', 'read SAP'), T('每 2 s', 'every 2 s')),
        ('msg', 'wms', 'sap', 'S2  GET 未过账入库交货', "Status eq 'A'", False),
        ('msg', 'sap', 'wms', T("200  1800000001，工厂 1000 / 库存地点 FG00", '200  1800000001, plant 1000 / sloc FG00'), None, True),
        ('phase', '③', T('内部', 'inside'), T('先不派车', 'not yet')),
        ('ref', T('WMS 内部：收货位登记托盘 4711，上架运输单改为 STARTED。这一步不出 WMS。',
                  'Inside WMS: register pallet 4711 and start the putaway order. This step stays inside the WMS.')),
        ('phase', '④', T('派车', 'dispatch'), T('内部单之后', 'then')),
        ('msg', 'wms', 'fleet', 'F4  POST transportOrders/INB-1800000001', T('GoodsReceipt 装货 → Storage-01 卸货，指定 Vehicle-01', 'GoodsReceipt load → Storage-01 unload, Vehicle-01'), False),
        ('msg', 'fleet', 'wms', '200  DISPATCHABLE', None, True),
        ('msg', 'fleet', 'car', T('派车，逐段下发', 'dispatch'), 'Park → GR → Store', False),
        ('phase', '⑤', T('执行', 'run'), T('回报车队', 'reports')),
        ('loop_start', T('loop  [每 2 s，直到 Fleet Manager 订单 FINISHED]', 'loop  [every 2 s until FINISHED]')),
        ('msg', 'car', 'fleet', T('回报位置与动作完成', 'position / operation reports'), None, True),
        ('msg', 'wms', 'fleet', 'F5  GET transportOrders/INB-1800000001', None, False),
        ('msg', 'fleet', 'wms', '200  BEING_PROCESSED  …  FINISHED', None, True),
        ('loop_end',),
        ('phase', '⑥', T('过账', 'post'), T('卸货之后', 'after unload')),
        ('ref', T('WMS 内部：上架运输单改为 FINISHED，托盘改到存储位。然后才调用 SAP 过账。',
                  'Inside WMS: finish the putaway order and move the pallet to storage. Then call SAP.')),
        ('msg', 'wms', 'sap', 'S3  GET 交货 1800000001', None, False),
        ('msg', 'sap', 'wms', '200  ETag: W/"1"', None, True),
        ('msg', 'wms', 'sap', 'S4  HEAD 取 CSRF 令牌', None, False),
        ('msg', 'sap', 'wms', '200  x-csrf-token: <token>', None, True),
        ('msg', 'wms', 'sap', 'S5  POST PostGoodsReceipt', 'If-Match · X-CSRF-Token', False),
        ('msg', 'sap', 'wms', "200  OverallGoodsMovementStatus = 'C'", T('物料凭证 5000000001，移动类型 101', 'material document 5000000001, movement 101'), True),
    ]

    # 先算总高度
    def height_of(step):
        kind = step[0]
        if kind == 'msg':
            return 6.6 if step[4] else 4.6
        return {'phase': 1.6, 'ref': 8.0, 'loop_start': 5.0, 'loop_end': 2.2, 'opt_start': 5.0, 'opt_end': 1.6}[kind]

    head = 18
    H = head + 4 + sum(height_of(s) for s in steps) + 10
    fig, ax = canvas(W, H)

    for key, (x, color, name, port) in lanes.items():
        box(ax, x - 10.5, 3, 21, 11, fc=tint(color, 0.85), ec=color, lw=0.8)
        text(ax, x, 6.6, name, size=6.6, weight='bold', ha='center')
        text(ax, x, 10.4, port, size=5.6, ha='center', color=GREY)
        ax.plot([x, x], [14, H - 8], color=LINE, lw=0.5, ls=(0, (2, 2)), zorder=0)

    y = head + 2
    phase_start = None
    phase_info = None
    bands = []
    frames = []
    for step in steps:
        kind = step[0]
        if kind == 'phase':
            if phase_info:
                bands.append((phase_start, y, phase_info))
            phase_start, phase_info = y, step[1:]
            y += height_of(step)
        elif kind == 'ref':
            box(ax, 17, y, 160, 6, fc='white', ec=GREY, lw=0.5, r=0.4, z=4)
            text(ax, 19, y + 3, step[1], size=5.8, color=INK, z=6)
            y += height_of(step)
        elif kind in ('loop_start', 'opt_start'):
            frames.append((kind, y, step[1]))
            y += height_of(step)
        elif kind in ('loop_end', 'opt_end'):
            start_kind, y0, label = frames.pop()
            inset = 0 if start_kind == 'loop_start' else 4
            x0, x1 = 17 + inset + (55 if start_kind == 'opt_start' else 0), 178 - inset
            yb = y + (0.6 if start_kind == 'opt_start' else 0)
            ax.add_patch(Rectangle((x0, y0 + 1), x1 - x0, yb - y0 - 1, fc='none', ec=INK if start_kind == 'loop_start' else GREY,
                                   lw=0.55, zorder=4))
            w = len(label) * 1.05 + 4
            ax.add_patch(Polygon([(x0, y0 + 1), (x0 + w, y0 + 1), (x0 + w, y0 + 3.6), (x0 + w - 1.4, y0 + 5), (x0, y0 + 5)],
                                 closed=True, fc='white', ec=INK if start_kind == 'loop_start' else GREY, lw=0.55, zorder=5))
            text(ax, x0 + 1.2, y0 + 3, label, size=5.6, weight='bold', z=6,
                 color=INK if start_kind == 'loop_start' else GREY)
            y += height_of(step)
        else:
            src, dst, label, detail, reply = step[1:6]
            color = step[6] if len(step) > 6 else INK
            yy = y + 3.2
            x1, x2 = X[src], X[dst]
            arrow(ax, x1, yy, x2, yy, color=GREY if reply else color, dashed=reply)
            other = X[dst] if src == 'wms' else X[src]
            if other > X['wms']:
                anchor, ha = X['wms'] + 1.5, 'left'
            else:
                anchor, ha = X['wms'] - 1.5, 'right'
            if src == 'car' or dst == 'car':
                anchor, ha = (min(x1, x2) + max(x1, x2)) / 2, 'center'
            text(ax, anchor, yy - 1.5, label, size=5.9, ha=ha, color=GREY if reply and color == INK else color,
                 mono=True, bg='white')
            if detail:
                text(ax, anchor, yy + 1.9, detail, size=5.3, ha=ha, color=GREY, bg='white')
            y += height_of(step)
    bands.append((phase_start, y, phase_info))

    for i, (y0, y1, (num, name, when)) in enumerate(bands):
        ax.add_patch(Rectangle((1, y0), 177, y1 - y0, fc='#F4F4F4' if i % 2 else 'white', ec='none', zorder=-1))
        text(ax, 2, y0 + 2.6, f'{num} {name}', size=6, weight='bold')
        text(ax, 2, y0 + 5.6, when, size=5.4, color=GREY)

    text(ax, 2, H - 4.2, T('实线为请求，虚线为响应或回报。这张图按入库交货的调用顺序画，时间以重新采样后的 run_trace.json 为准。编号与图 1 一致。',
                          'Solid: request; dashed: response/report. Sequence of one inbound delivery. '
                          'Durations come from run_trace.json after a fresh recording. IDs as in Fig. 1.'),
         size=5.6, color=GREY)
    save(fig, 'fig2_sequence')


# ---------------------------------------------------------------- 图 3

def figure_confirm():
    W, H = 180, 92
    fig, ax = canvas(W, H)
    panel(ax, 1.5, 2, 'a')
    text(ax, 6, 4.6, T('一次收货过账依次经过的校验', 'Checks applied to one goods receipt'), size=7, weight='bold')

    box(ax, 18, 9, 62, 7, fc=tint(WMS, 0.85), ec=WMS, lw=0.7)
    text(ax, 49, 12.5, "POST …/PostGoodsReceipt?DeliveryDocument='1800000001'", size=5.5, ha='center', mono=True)

    checks = [
        (T('网关', 'gateway'), T('X-CSRF-Token 正确？', 'valid X-CSRF-Token?'), '403  x-csrf-token: Required'),
        (T('网关', 'gateway'), T('带了 If-Match？', 'If-Match present?'), '428  Precondition Required'),
        ('OData', T('ETag 是最新的？', 'ETag current?'), '412  Precondition Failed'),
        (T('业务', 'business'), T("交货单存在？", 'delivery exists?'), T('404  交货单不存在', '404  unknown delivery')),
        (T('业务', 'business'), T("OverallGoodsMovementStatus = 'A' ？", "OverallGoodsMovementStatus = 'A' ?"), T('400  只有未过账可以收货', '400  already posted')),
    ]
    y = 20
    prev = 16
    layer_spans = {}
    for layer, question, reject in checks:
        tall = '\n' in question
        h = 9 if tall else 6.4
        arrow(ax, 49, prev, 49, y)
        box(ax, 26, y, 46, h, fc='white', ec=INK, lw=0.6, r=0.6)
        text(ax, 49, y + h / 2, question, size=5.8, ha='center')
        arrow(ax, 72, y + h / 2, 82, y + h / 2, color=BAD)
        text(ax, 77, y + h / 2 - 1.4, T('否', 'no'), size=5.4, ha='center', color=BAD)
        box(ax, 82, y + h / 2 - (3.9 if '\n' in reject else 2.6), 34, 7.8 if '\n' in reject else 5.2,
            fc=tint(BAD, 0.9), ec=BAD, lw=0.5, r=0.5)
        text(ax, 84, y + h / 2, reject, size=5.4, mono=True, color=BAD)
        text(ax, 50.5, y - 1.8, T('是', 'yes'), size=5.4, color=GREY) if prev != 16 else None
        span = layer_spans.setdefault(layer, [y, y + h])
        span[1] = y + h
        prev = y + h
        y += h + 4
    arrow(ax, 49, prev, 49, y)
    text(ax, 50.5, y - 1.8, T('是', 'yes'), size=5.4, color=GREY)
    box(ax, 26, y, 46, 6.4, fc=tint(WMS, 0.85), ec=WMS, lw=0.7, r=0.6)
    text(ax, 49, y + 3.2, "200  OverallGoodsMovementStatus = 'C'", size=5.6, ha='center', mono=True)

    for layer, (y0, y1) in layer_spans.items():
        ax.plot([20, 20], [y0, y1], color=GREY, lw=0.8)
        ax.plot([20, 21.2], [y0, y0], color=GREY, lw=0.8)
        ax.plot([20, 21.2], [y1, y1], color=GREY, lw=0.8)
        text(ax, 18.6, (y0 + y1) / 2, layer, size=5.4, ha='right', color=GREY)
    text(ax, 6, H - 3, T('校验都在 sap/server.js：令牌、If-Match、ETag，以及只有状态 A 可以过账。',
                         'All checks are in sap/server.js: token, If-Match, ETag, and only status A can be posted.'),
         size=5.4, color=GREY)

    # ---- b 状态机
    panel(ax, 121, 2, 'b')
    text(ax, 126, 4.6, T('状态', 'States'), size=7, weight='bold')

    def state(x, y, label, code, color):
        box(ax, x, y, 18, 8, fc=tint(color, 0.85), ec=color, lw=0.7, r=2.5)
        text(ax, x + 9, y + 2.9, label, size=6, ha='center', weight='bold')
        text(ax, x + 9, y + 5.8, code, size=5.6, ha='center', mono=True)

    text(ax, 126, 12, 'A_InbDeliveryHeader', size=6.2, weight='bold', mono=True)
    ax.add_patch(plt.Circle((128, 24), 1.1, color=INK, zorder=4))
    arrow(ax, 129.1, 24, 134, 24)
    text(ax, 131.5, 27.4, 'S6', size=5.2, ha='center', color=GREY)
    state(134, 20, T('未过账', 'open'), "'A'", SAP)
    state(160, 20, T('已过账', 'posted'), "'C'", WMS)
    arrow(ax, 152, 24, 160, 24)
    text(ax, 156, 21.2, 'S5', size=5.6, ha='center', weight='bold', color=SAP)

    text(ax, 126, 46, 'A_MaterialDocument', size=6.2, weight='bold', mono=True)
    state(134, 54, T('尚未产生', 'absent'), '—', GREY)
    state(160, 54, T('收货凭证', 'GR doc'), '101', WMS)
    arrow(ax, 152, 58, 160, 58, dashed=True)
    text(ax, 126, 74.5, T('过账收货时同时写入物料凭证 5000000001。', 'Goods receipt also writes material document 5000000001.'), size=5.4, color=GREY)
    text(ax, 126, 77.8, T('出库交货同样从 A 到 C，移动类型是 601。', 'An outbound delivery goes A to C with movement 601.'), size=5.4, color=GREY)
    text(ax, 126, 82.4, T('实线：过账调用；虚线：同时生成物料凭证', 'solid: posting call; dashed: material document'), size=5.4, color=GREY)
    save(fig, 'fig3_confirm')


# ---------------------------------------------------------------- 图 4

def figure_architecture():
    W, H = 180, 146
    fig, ax = canvas(W, H)
    panel(ax, 1.5, 2, 'a')
    text(ax, 6, 4.6, T('串行：WMS 先调用 SAP，再调用 Fleet Manager', 'Serial: the WMS calls SAP, then Fleet Manager'),
         size=7, weight='bold')

    layers = [
        (9, 16, SAP, T('L4 业务计划', 'L4 business\nplanning'), 'SAP S/4HANA ERP',
         T('决定收什么、收多少、进哪个工厂和库存地点', 'decides what, how many, which plant and storage location')),
        (29, 22, WMS, T('L3 仓储执行', 'L3 warehouse\nexecution'), 'WMS',
         T('先调 SAP；库位和托盘在内部', 'calls SAP first; bins stay inside')),
        (55, 16, TCS, T('L2 车队', 'L2 fleet\nmanager'), 'Fleet Manager',
         T('本实验用 openTCS，分配车辆和路线', 'openTCS in this lab; routes the vehicle')),
        (75, 14, CAR, T('L1 设备', 'L1 device'), 'Vehicle-01 (Loopback)',
         T('按段行驶，回报位置和动作', 'drives segments, reports position')),
    ]
    for y, h, color, level, name, role in layers:
        text(ax, 5, y + h / 2, level, size=6.2, weight='bold', color=color)
        box(ax, 30, y, 58, h, fc=tint(color, 0.88), ec=color, lw=0.8)
        ax.add_patch(Rectangle((30.4, y + 0.4), 1.3, h - 0.8, fc=color, ec='none', zorder=3))
        text(ax, 33.5, y + h / 2 - 2, name, size=6.8, weight='bold')
        text(ax, 33.5, y + h / 2 + 2.2, role, size=5.6, color=GREY)
    for (y1, h1, *_), (y2, *_rest) in zip(layers, layers[1:]):
        arrow(ax, 52, y1 + h1, 52, y2, color=INK)
        arrow(ax, 68, y2, 68, y1 + h1, color=GREY, dashed=True)
    text(ax, 50.5, 27.2, T('WMS 调用', 'WMS calls'), size=5.2, ha='right', color=WMS)
    text(ax, 69.5, 27.2, T('WMS 回写', 'WMS posts'), size=5.2, color=GREY)
    text(ax, 50.5, 53.2, T('然后调用', 'then calls'), size=5.2, ha='right', color=TCS)
    text(ax, 69.5, 53.2, T('完成回报', 'done'), size=5.2, color=GREY)

    # 右侧：同一笔搬运在每一层变成什么对象
    text(ax, 96, 9.5, T('同一笔入库在每层的对象（交货 1800000001）', 'The same receipt at each layer (delivery 1800000001)'),
         size=6.2, weight='bold')
    objects = [
        (12, SAP, 'InboundDelivery 1800000001', 'MAT-100 × 1 EA · Plant 1000 / SLoc FG00 · no bin'),
        (35, WMS, T('上架运输单', 'putaway order'),
         T('托盘 4711：收货位 → 存储位', 'pallet 4711: GR → storage')),
        (58, TCS, 'Fleet Manager INB-1800000001', T('GoodsReceipt 装货 → Storage-01 卸货', 'GoodsReceipt load → Storage-01 unload')),
        (78, CAR, T('行驶段', 'drive orders'), 'Point-Park → Point-GR → Point-Store'),
    ]
    for y, color, title, detail in objects:
        box(ax, 96, y, 80, 9, fc='white', ec=color, lw=0.6, r=0.6)
        text(ax, 98, y + 2.8, title, size=6, weight='bold', mono=True, color=color)
        text(ax, 98, y + 6.3, detail, size=5.2, mono=True, color=INK)
        arrow(ax, 88, y + 4.5, 96, y + 4.5, color=LINE)
    text(ax, 96, 100, T('越往下越具体：业务数量 → 托盘与库位 → 位置与动作 → 路线上的点。',
                        'Lower layers are more concrete: quantities → pallets/locations → stations → route points.'),
         size=5.4, color=GREY)

    # ---- b 名字和状态的换算
    top = 106
    panel(ax, 1.5, top, 'b')
    text(ax, 6, top + 2.6, T('WMS 自己的库位和状态对照', 'Mappings kept by the WMS'), size=7, weight='bold')

    def table(x, y, widths, header, rows, colors):
        cx = [x]
        for w in widths:
            cx.append(cx[-1] + w)
        ax.plot([x, cx[-1]], [y, y], color=INK, lw=0.8)
        for i, name in enumerate(header):
            text(ax, cx[i] + 1, y + 2.4, name, size=5.8, weight='bold')
        ax.plot([x, cx[-1]], [y + 4.6, y + 4.6], color=INK, lw=0.5)
        yy = y + 4.6
        for row, color in zip(rows, colors):
            ax.add_patch(Rectangle((x, yy), 1.0, 5.6, fc=color, ec='none', zorder=2))
            for i, cell in enumerate(row):
                text(ax, cx[i] + (2 if i == 0 else 1), yy + 2.8, cell, size=5.3, mono=i > 0)
            yy += 5.6
            ax.plot([x, cx[-1]], [yy, yy], color='#DDDDDD', lw=0.3)
        ax.plot([x, cx[-1]], [yy, yy], color=INK, lw=0.8)

    table(4, top + 6, [16, 26, 24, 22],
          [T('层', 'Layer'), T('单号', 'Identifier'), T('源', 'Source'), T('目标', 'Target')],
          [['SAP', '1800000001', '1000 / FG00', T('收货 101', 'GR 101')],
           ['WMS', T('<UUID，每次不同>', '<UUID, per run>'), 'FGIN/GR/0001/…', 'FGIN/ST/0001/…'],
           ['Fleet Manager', 'INB-1800000001', 'GoodsReceipt', 'Storage-01'],
           [T('车辆', 'Vehicle'), 'Vehicle-01', 'Point-GR', 'Point-Store']],
          [SAP, WMS, TCS, CAR])
    table(96, top + 6, [12, 10, 19, 27, 12],
          [T('阶段', 'Stage'), 'SAP', 'WMS', 'Fleet Mgr', T('车辆', 'Vehicle')],
          [[T('等待', 'waiting'), "'A'", '—', '—', 'IDLE'],
           [T('上架', 'putaway'), "'A'", 'STARTED', 'BEING_PROCESSED', 'EXEC.'],
           [T('过账', 'posted'), "'C'", 'FINISHED', 'FINISHED', 'IDLE'],
           [T('停手', 'parked'), "'A'", 'ONFAILURE', 'FAILED', '—']],
          [LINE, BRIDGE, WMS, GREY])
    text(ax, 4, H - 3.6, T("入库交货在车上的时候仍是 'A'，SAP 看不到仓位；执行中这一段只能在 WMS 和 Fleet Manager 看到。",
                           "The inbound delivery stays 'A' while the vehicle moves, and SAP has no bin. "
                           "Progress is visible only in the WMS and Fleet Manager."),
         size=5.4, color=GREY)
    save(fig, 'fig4_architecture')


# ---------------------------------------------------------------- 图 5

WAITING = {'', 'RAW', 'DISPATCHABLE', 'PRISTINE', 'IDLE', 'CREATED', 'RELEASED'}
ACTIVE = {'STARTED', 'BEING_PROCESSED', 'TRAVELLING', 'EXECUTING', 'IN_PROCESS'}
DONE = {'C', 'FINISHED', 'CONFIRMED'}
FILL = {'absent': '#F2F2F2', 'waiting': '#D6E4F0', 'active': '#F7C873', 'done': '#9AD7BF', 'up': '#C9C9C9'}
PLACES = {'Point-Park': ('#E4E4E4', 'Park'), 'Point-GR': (tint(SAP, 0.6), 'GR'),
          'Point-Store': (tint(WMS, 0.5), 'Store')}


def segments(samples, chain, key):
    out = []
    for i, sample in enumerate(samples):
        t = sample[chain]['t']
        value = sample[chain][key]
        if isinstance(value, list):
            value = tuple(value)
        if out and out[-1][2] == value:
            continue
        if out:
            out[-1][1] = t
        out.append([t, None, value])
    if out:
        out[-1][1] = samples[-1][chain]['t']
    return out


def style(kind, value):
    if kind == 'up':
        return ('up', T('可用', 'up')) if value else ('absent', '')
    if kind == 'place':
        if value in PLACES:
            return PLACES[value]
        return 'absent', ''
    if kind == 'pallet':
        if not value:
            return 'absent', ''
        return ('waiting', 'GR') if '/GR/' in value else ('done', 'ST')
    if value is None:
        return 'absent', ''
    label = T("未清 ''", "open ''") if value == '' else str(value)
    if value in DONE:
        return 'done', label
    if value in ACTIVE:
        return 'active', label
    return 'waiting', label


def timeline(ax, samples, chain, lanes, x0, x1, y, tmax, ticks, events, lane_h=4.2):
    scale = (x1 - x0) / tmax
    for i, (name, key, kind) in enumerate(lanes):
        yy = y + i * (lane_h + 1.0)
        text(ax, x0 - 1.5, yy + lane_h / 2, name, size=5.6, ha='right')
        if kind.startswith('dest'):
            index = int(kind[-1])
            data = [[a, b, (v[index] if v and len(v) > index else None)] for a, b, v in segments(samples, chain, key)]
            merged = []
            for seg in data:
                if merged and merged[-1][2] == seg[2]:
                    merged[-1][1] = seg[1]
                else:
                    merged.append(seg)
            data, kind = merged, 'state'
        else:
            data = segments(samples, chain, key)
        for a, b, value in data:
            color, label = style(kind, value)
            fill = FILL.get(color, color)
            xa, xb = x0 + a * scale, x0 + min(b, tmax) * scale
            if xb <= xa:
                continue
            ax.add_patch(Rectangle((xa, yy), xb - xa, lane_h, fc=fill, ec='white', lw=0.3,
                                   hatch='/////' if color == 'absent' else None, zorder=2))
            if label and (xb - xa) > len(label) * 1.05 + 1:
                text(ax, (xa + xb) / 2, yy + lane_h / 2, label, size=5.0, ha='center', mono=True, z=4)
    bottom = y + len(lanes) * (lane_h + 1.0)
    ax.plot([x0, x1], [bottom, bottom], color=INK, lw=0.6)
    for tick in ticks:
        x = x0 + tick * scale
        ax.plot([x, x], [bottom, bottom + 1], color=INK, lw=0.6)
        text(ax, x, bottom + 2.6, f'{tick:g}', size=5.6, ha='center')
    text(ax, (x0 + x1) / 2, bottom + 6, T('自 docker compose up 起的时间 (s)', 'time since docker compose up (s)'),
         size=5.8, ha='center')
    for t, label, row in events:
        x = x0 + t * scale
        ax.plot([x, x], [y - 1.5 - row * 3.2, bottom], color=INK, lw=0.45, ls=(0, (2, 1.5)), zorder=5)
        text(ax, x, y - 2.6 - row * 3.2, label, size=5.2, ha='center', bg='white', z=6)
    return bottom


def first(samples, chain, key, predicate):
    return next(s[chain]['t'] for s in samples if predicate(s[chain][key]))


def figure_run_state():
    data = json.loads((OUT / 'run_trace.json').read_text(encoding='utf-8'))
    if data.get('meta', {}).get('architecture') != 'erp-wms':
        print('skip fig5: run_trace.json 仍是上一套架构的采样')
        return
    samples = data['samples']
    W, H = 180, 181
    fig, ax = canvas(W, H)

    full_lanes = [
        ('SAP', 'sapUp', 'up'),
        ('openTCS', 'tcsUp', 'up'),
        ('OpenWMS TMS', 'tmsUp', 'up'),
        ('OpenWMS Common', 'commonUp', 'up'),
        (T('SAP 入库交货', 'SAP inbound delivery'), 'sap', 'state'),
        (T('物料凭证', 'material document'), 'materialDocument', 'state'),
        (T('WMS 运输单', 'WMS transport order'), 'wms', 'state'),
        (T('托盘位置', 'pallet location'), 'pallet', 'pallet'),
        (T('车队订单', 'fleet order'), 'fleet', 'state'),
        (T('路段 1 收货位装货', 'leg 1 goods-receipt load'), 'destinations', 'dest0'),
        (T('路段 2 存储位卸货', 'leg 2 storage unload'), 'destinations', 'dest1'),
        (T('车辆状态', 'vehicle state'), 'vehicle', 'state'),
        (T('车辆位置', 'vehicle position'), 'position', 'place'),
    ]
    full = 'full'
    t_ready = max(first(samples, full, k, bool) for k in ('sapUp', 'tcsUp', 'tmsUp', 'commonUp'))
    t_exec = first(samples, full, 'fleet', lambda v: v == 'BEING_PROCESSED')
    t_in = first(samples, full, 'position', lambda v: v == 'Point-GR')
    t_pick = first(samples, full, 'position', lambda v: v == 'Point-Store')
    t_conf = first(samples, full, 'sap', lambda v: v == 'C')
    tmax_full = samples[-1][full]['t']
    panel(ax, 1.5, 2, 'a')
    text(ax, 6, 4.6, T('完整版：SAP ERP + OpenWMS + openTCS', 'Full stack: SAP ERP + OpenWMS + openTCS'),
         size=7, weight='bold')
    bottom = timeline(ax, samples, full, full_lanes, 40, 176, 17, tmax_full, range(0, int(tmax_full) + 1, 5), [
        (t_ready, T(f'服务全部可用 {t_ready:.1f}', f'all up {t_ready:.1f}'), 0),
        (t_exec, T(f'开始上架 {t_exec:.1f}', f'putaway {t_exec:.1f}'), 1),
        (t_in, T(f'到收货位 {t_in:.1f}', f'at GR {t_in:.1f}'), 0),
        (t_pick, T(f'到存储位 {t_pick:.1f}', f'at storage {t_pick:.1f}'), 1),
        (t_conf, T(f'收货过账 {t_conf:.1f}', f'goods receipt {t_conf:.1f}'), 2),
    ])
    text(ax, 38.5, 17 - 1.8, T('服务可用', 'service up'), size=5.2, ha='right', color=GREY)

    py_lanes = [
        (T('SAP 任务', 'SAP task'), 'sap', 'state'),
        (T('WMS 运输单', 'WMS transport order'), 'wms', 'state'),
        (T('托盘位置', 'pallet location'), 'pallet', 'pallet'),
        (T('车队订单', 'fleet order'), 'fleet', 'state'),
        (T('路段 1 收货位装货', 'leg 1 goods-receipt load'), 'destinations', 'dest0'),
        (T('路段 2 存储位卸货', 'leg 2 storage unload'), 'destinations', 'dest1'),
        (T('车辆状态', 'vehicle state'), 'vehicle', 'state'),
        (T('车辆位置', 'vehicle position'), 'position', 'place'),
    ]
    py = 'python'
    py_in = first(samples, py, 'position', lambda v: v == 'Point-GR')
    py_pick = first(samples, py, 'position', lambda v: v == 'Point-Store')
    py_conf = first(samples, py, 'sap', lambda v: v == 'C')
    top = bottom + 13
    panel(ax, 1.5, top - 2, 'b')
    text(ax, 6, top + 0.6, T('Python 简化版（同一次 docker compose up）', 'Python simplified chain (same docker compose up)'),
         size=7, weight='bold')
    py_tmax = py_conf + 1.5
    bottom = timeline(ax, samples, py, py_lanes, 40, 176, top + 13, py_tmax, range(0, int(py_tmax) + 1, 1), [
        (py_in, T(f'到收货位 {py_in:.1f}', f'at GR {py_in:.1f}'), 0),
        (py_pick, T(f'到存储位 {py_pick:.1f}', f'at storage {py_pick:.1f}'), 1),
        (py_conf, T(f'过账 {py_conf:.1f}', f'posted {py_conf:.1f}'), 0),
    ])

    # 图例
    y = bottom + 10
    items = [('absent', T('不可达或未创建', 'unreachable / absent')), ('up', T('服务可用', 'service up')),
             ('waiting', T('等待', 'waiting')), ('active', T('执行中', 'running')), ('done', T('完成', 'done'))]
    x = 40
    for key, label in items:
        ax.add_patch(Rectangle((x, y - 1.6), 5, 3.2, fc=FILL[key], ec='#BBBBBB', lw=0.3,
                               hatch='/////' if key == 'absent' else None))
        text(ax, x + 6.2, y, label, size=5.6)
        x += 6.2 + len(label) * (2.1 if LANG == 'zh' else 1.05) + 4
    x = 40
    y += 5
    for point, (color, label) in PLACES.items():
        ax.add_patch(Rectangle((x, y - 1.6), 5, 3.2, fc=color, ec='#BBBBBB', lw=0.3))
        text(ax, x + 6.2, y, point, size=5.6, mono=True)
        x += 30
    journal = data['journals']['full']
    stamps = {e['message']: e['at'] for e in journal}
    taken = datetime.fromisoformat(next(v for k, v in stamps.items() if k.startswith('接手')).replace('Z', '+00:00'))
    confirmed = datetime.fromisoformat(next(v for k, v in stamps.items() if '过账收货' in k or k.startswith('上架完成')).replace('Z', '+00:00'))
    span = (confirmed - taken).total_seconds()
    gaps = [b[full]['t'] - a[full]['t'] for a, b in zip(samples, samples[1:])]
    text(ax, 4, y + 6.5, T(f'数据：docs/figures/run_trace.json，{data["meta"]["startedAt"]} 开始的一次运行，共 {len(samples)} 次采样，'
                           f'间隔中位数 {sorted(gaps)[len(gaps) // 2]:.1f} s、最大 {max(gaps):.1f} s。',
                           f'Data: docs/figures/run_trace.json, run started {data["meta"]["startedAt"]}, {len(samples)} samples, '
                           f'median interval {sorted(gaps)[len(gaps) // 2]:.1f} s, max {max(gaps):.1f} s.'),
         size=5.4, color=GREY)
    text(ax, 4, y + 10.3, T(f'完整版 WMS 日志：接手入库交货到收货过账 {span:.1f} s。Python 版往往在第一次采样前已经过账。',
                           f'WMS journal (full stack): delivery taken to goods receipt {span:.1f} s. '
                           'The Python chain often posts before the first sample.'),
         size=5.4, color=GREY)
    save(fig, 'fig5_run_state')


def main():
    global LANG
    langs = sys.argv[1:] or ['zh', 'en']
    for lang in langs:
        LANG = lang
        figure_interfaces()
        figure_sequence()
        figure_confirm()
        figure_architecture()
        figure_run_state()
        print(f'{lang}: {OUT}')


if __name__ == '__main__':
    main()

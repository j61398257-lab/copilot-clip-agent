"""在一次真实运行中约每 0.2 s 采样两条链路的状态，写入 run_trace.json。

用法：先 docker compose down，再启动本脚本，脚本会自己执行 docker compose up -d，
两条链路的入库交货都过账后再多记 3 s。时间零点是执行 up 的时刻。
"""
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'run_trace.json'
INB = 'http://localhost:4004/sap/opu/odata/sap/API_INBOUND_DELIVERY_SRV;v=0002'
DOC = '1800000001'
PALLET = '00000000000000004711'
TU = 'application/vnd.openwms.transport-unit-v1+json'
opener = build_opener(ProxyHandler({}))


def get(url, accept='application/json'):
    try:
        with opener.open(Request(url, headers={'Accept': accept}), timeout=0.6) as response:
            return json.loads(response.read().decode('utf-8') or 'null'), time.time()
    except Exception:
        return None, time.time()


def urls(pkey):
    delivery = f"{INB}/A_InbDeliveryHeader('{DOC}')"
    items = {
        'full.sap': delivery,
        'full.wmsApp': 'http://localhost:8090/status',
        'full.commonUp': 'http://localhost:8120/actuator/info',
        'full.tmsUp': 'http://localhost:8131/actuator/info',
        'full.tcsUp': 'http://localhost:55200/v1/kernel/version',
        'full.pallet': (f'http://localhost:8120/v1/transport-units?bk={PALLET}', TU),
        'full.fleet': f'http://localhost:55200/v1/transportOrders/INB-{DOC}',
        'full.vehicle': 'http://localhost:55200/v1/vehicles/Vehicle-01',
        'py.sap': 'http://localhost:9004/inbound-deliveries',
        'py.wms': 'http://localhost:9120/transport-orders',
        'py.pallet': f'http://localhost:9120/transport-units/{PALLET}',
        'py.fleet': 'http://localhost:9200/transport-orders',
        'py.vehicles': 'http://localhost:9200/vehicles',
        'py.vehicle': 'http://localhost:9300/state',
        'py.wmsApp': 'http://localhost:9120/status',
    }
    if pkey:
        items['full.wms'] = f'http://localhost:8131/v1/transport-orders/{quote(pkey, safe="")}'
    return items


def fetch(pool, items):
    def one(item):
        key, target = item
        url, accept = target if isinstance(target, tuple) else (target, 'application/json')
        return key, get(url, accept)
    return dict(pool.map(one, items.items()))


def value(raw, key):
    return (raw.get(key) or (None, 0))[0]


def stamp(raw, prefix):
    times = [t for key, (_, t) in raw.items() if key.startswith(prefix)]
    return max(times) if times else time.time()


def reduce_full(raw):
    sap = ((value(raw, 'full.sap') or {}).get('d')) or {}
    wms, pallet, fleet, vehicle = (value(raw, f'full.{k}') or {} for k in ('wms', 'pallet', 'fleet', 'vehicle'))
    actual = pallet.get('actualLocation')
    return {
        'sapUp': value(raw, 'full.sap') is not None,
        'commonUp': value(raw, 'full.commonUp') is not None,
        'tmsUp': value(raw, 'full.tmsUp') is not None,
        'tcsUp': value(raw, 'full.tcsUp') is not None,
        'sap': sap.get('OverallGoodsMovementStatus'),
        'materialDocument': sap.get('MaterialDocument'),
        'wms': wms.get('state'),
        'pallet': actual.get('locationId') if isinstance(actual, dict) else None,
        'fleet': fleet.get('state'),
        'destinations': [d.get('state') for d in fleet.get('destinations') or []],
        'position': vehicle.get('currentPosition'),
        'vehicle': vehicle.get('state'),
    }


def reduce_python(raw):
    deliveries = value(raw, 'py.sap')
    delivery = next((t for t in deliveries if t.get('delivery') == DOC), {}) if isinstance(deliveries, list) else {}
    orders = value(raw, 'py.wms')
    fleet_orders = value(raw, 'py.fleet')
    fleet = next((o for o in fleet_orders if o.get('name') == f'INB-{DOC}'), {}) if isinstance(fleet_orders, list) else {}
    vehicles = value(raw, 'py.vehicles')
    car = next((v for v in vehicles if v.get('name') == 'Vehicle-01'), {}) if isinstance(vehicles, list) else {}
    own = value(raw, 'py.vehicle') or {}
    return {
        'sapUp': deliveries is not None,
        'sap': delivery.get('goodsMovementStatus'),
        'materialDocument': delivery.get('materialDocument'),
        'wms': orders[-1].get('state') if isinstance(orders, list) and orders else None,
        'pallet': (value(raw, 'py.pallet') or {}).get('location'),
        'fleet': fleet.get('state'),
        'destinations': [d.get('state') for d in fleet.get('destinations') or []],
        'position': own.get('position') or car.get('position'),
        'vehicle': car.get('state'),
    }


def main():
    samples = []
    journals = {}
    links = {}
    done_at = None
    with ThreadPoolExecutor(max_workers=20) as pool:
        start = time.time()
        subprocess.Popen(['docker', 'compose', 'up', '-d'], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while time.time() - start < 300:
            began = time.time()
            raw = fetch(pool, urls((links.get(DOC) or {}).get('wms')))
            wms_app = value(raw, 'full.wmsApp')
            if wms_app:
                links = wms_app.get('links') or {}
                journals['full'] = wms_app.get('journal')
            py_wms = value(raw, 'py.wmsApp')
            if py_wms:
                journals['python'] = py_wms.get('journal')
            sample = {
                'full': {'t': stamp(raw, 'full.') - start, **reduce_full(raw)},
                'python': {'t': stamp(raw, 'py.') - start, **reduce_python(raw)},
            }
            samples.append(sample)
            if done_at is None and sample['full']['sap'] == 'C' and sample['python']['sap'] == 'C':
                done_at = time.time()
            if done_at and time.time() - done_at > 3:
                break
            time.sleep(max(0.0, 0.2 - (time.time() - began)))
    meta = {
        'architecture': 'erp-wms',
        'startedAt': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start)),
        'interval': 0.2,
    }
    OUT.write_text(json.dumps({'meta': meta, 'samples': samples, 'journals': journals}, ensure_ascii=False, indent=1),
                   encoding='utf-8')
    print(f'{len(samples)} samples, {samples[-1]["full"]["t"]:.1f} s -> {OUT}')


if __name__ == '__main__':
    main()

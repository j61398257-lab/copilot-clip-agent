import os
import time

from .lab import PORTS, start

LINKS = [
    ('SAP 入库交货', 'sap', '/inbound-deliveries'),
    ('SAP 物料凭证', 'sap', '/material-documents'),
    ('WMS 运输单', 'wms', '/transport-orders'),
    ('WMS 托盘', 'wms', '/transport-units/00000000000000004711'),
    ('车队运输订单', 'fleet', '/transport-orders'),
    ('车队眼里的车辆', 'fleet', '/vehicles'),
    ('车辆自己', 'vehicle', '/state'),
    ('WMS 调用 SAP 的进度', 'wms', '/status'),
]


def main():
    print('四个服务启动中，Ctrl+C 停止。浏览器可以看：')
    for label, system, path in LINKS:
        print(f'  {label}：http://localhost:{PORTS[system]}{path}')
    running = start(host=os.environ.get('HOST', '127.0.0.1'))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        running.stop()


main()

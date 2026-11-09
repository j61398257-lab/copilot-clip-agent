import threading
import time

from .web import HttpError, call, log


class Vehicle:
    def __init__(self, name='Vehicle-01', position='Point-Park', step_seconds=1.0):
        self.name = name
        self.position = position
        self.step_seconds = step_seconds
        self.drive_order = None
        self.fleet_url = None
        self.lock = threading.Lock()

    def routes(self):
        return [
            ('GET', r'/state', self.get_state),
            ('POST', r'/drive-orders', self.accept),
        ]

    def connect(self, fleet_url, own_url):
        self.fleet_url = fleet_url
        self._report('online', url=own_url)

    def get_state(self, request):
        with self.lock:
            return {'name': self.name, 'position': self.position, 'driveOrder': self.drive_order}

    def accept(self, request):
        with self.lock:
            if self.drive_order:
                raise HttpError(409, f'{self.name} 还在执行上一段行驶任务')
            self.drive_order = request.body
        threading.Thread(target=self._drive, args=(request.body,), daemon=True).start()
        return 202, {'accepted': True}

    def _drive(self, drive_order):
        for point in drive_order['route']:
            time.sleep(self.step_seconds)
            with self.lock:
                self.position = point
            log('车辆', f'{self.name} 到达 {point}')
            self._report('moved')
        time.sleep(self.step_seconds)
        with self.lock:
            self.drive_order = None
        log('车辆', f'{self.name} 在 {self.position} 完成 {drive_order["operation"]}')
        self._report('done')

    def _report(self, event, **extra):
        call('POST', f'{self.fleet_url}/vehicles/{self.name}/reports',
             {'event': event, 'position': self.position, **extra})

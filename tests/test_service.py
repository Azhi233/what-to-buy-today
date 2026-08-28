import threading

from monitor_service import MonitorService


class DummyDb:
    pass


class DummyNotifier:
    pass


def test_stop_waits_for_worker_thread():
    service = MonitorService(DummyDb(), DummyNotifier())
    finished = threading.Event()

    def worker():
        service._stop_event.wait()
        finished.set()

    service._thread = threading.Thread(target=worker)
    service._thread.start()
    service.stop(timeout=1)

    assert finished.is_set()
    assert not service._thread.is_alive()

"""Stable CLI/import compatibility for :mod:`traffic_sim.confidence.report`."""

if __name__ == "__main__":
    from traffic_sim.confidence.report import write_report

    write_report()
else:
    import sys as _sys
    from traffic_sim.confidence import report as _implementation

    _sys.modules[__name__] = _implementation

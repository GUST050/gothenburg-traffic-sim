"""Stable CLI/import compatibility for :mod:`traffic_sim.confidence.loso`."""

if __name__ == "__main__":
    from traffic_sim.confidence.loso import main

    main()
else:
    import sys as _sys
    from traffic_sim.confidence import loso as _implementation

    _sys.modules[__name__] = _implementation

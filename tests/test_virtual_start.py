"""Where the copies of a Virtual cluster start (v1.7.2).

Centred on the real state of charge, so the copies average the real battery;
then the whole set shifts into 5-100 %, so the differences between the copies
stay the offsets that were set. Offsets 0 / -20 / +15 as on production.
"""

import pytest

from custom_components.zendure_restapi.const import OPT_CLONES, OPT_SOC_OFFSET_1, OPT_SOC_OFFSET_2, OPT_SOC_OFFSET_3
from custom_components.zendure_restapi.sim.cluster import VirtualCluster


def cluster(real: float, clones: int = 3, offset_2: float = -20, offset_3: float = 15,
            offset_1: float = 0) -> VirtualCluster:
    vc = VirtualCluster.__new__(VirtualCluster)
    values = {OPT_CLONES: clones, OPT_SOC_OFFSET_1: offset_1, OPT_SOC_OFFSET_2: offset_2, OPT_SOC_OFFSET_3: offset_3}
    vc.settings = type("S", (), {"get": staticmethod(lambda key: values.get(key))})()
    vc._source_soc = lambda: real
    vc.start_info, vc.start_offsets = {}, []
    return vc


def socs(vc: VirtualCluster) -> list[float]:
    return [round(s, 1) for s in vc.start_socs()]


def test_centred_on_the_real_soc_in_the_middle():
    vc = cluster(46)
    assert socs(vc) == [47.7, 27.7, 62.7]
    assert sum(vc.start_socs()) / 3 == pytest.approx(46)
    assert vc.start_info["start_shift"] == 0 and vc.start_info["start_centre"] == pytest.approx(1.67)


def test_low_real_soc_shifts_the_whole_set_up():
    vc = cluster(4)
    assert socs(vc) == [25.0, 5.0, 40.0]
    assert vc.start_info["start_shift"] == pytest.approx(19.33)
    assert vc.start_offsets == [21.0, 1.0, 36.0]


def test_high_real_soc_shifts_the_whole_set_down():
    vc = cluster(95)
    assert socs(vc) == [85.0, 65.0, 100.0]
    assert vc.start_info["start_shift"] == pytest.approx(-11.67)


def test_one_copy_is_the_real_soc():
    assert socs(cluster(46, clones=1)) == [46.0]
    assert socs(cluster(3, clones=1)) == [5.0]


def test_two_copies_centre_on_the_real_soc():
    assert socs(cluster(46, clones=2)) == [56.0, 36.0]


def test_a_set_wider_than_the_window_puts_the_lowest_on_five():
    assert socs(cluster(4, offset_2=-50, offset_3=50)) == [55.0, 5.0, 100.0]


def test_an_offset_on_copy_1_moves_the_centre():
    # +10 / -20 / +15 around 46: mean 1.67, copies 46 + 8.33 / -21.67 / +13.33.
    assert socs(cluster(46, offset_1=10)) == [54.3, 24.3, 59.3]
    assert sum(cluster(46, offset_1=10).start_socs()) / 3 == pytest.approx(46)


def test_one_copy_centres_its_own_offset_away():
    assert socs(cluster(46, clones=1, offset_1=25)) == [46.0]

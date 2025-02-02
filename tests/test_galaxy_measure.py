import pytest


class TestBasicGalaxyMeasure:
    @pytest.fixture(scope="class")
    def overrides(self, devices):
        overrides = dict(
            model="sleep_sdss_measure_simple",
            dataset="default" if devices.use_cuda else "cpu",
            training="cpu",
        )
        return overrides

    @pytest.fixture(scope="class")
    def trained_sleep(self, overrides, sleep_setup):
        return sleep_setup.get_trained_sleep(overrides)

    def test_simulated(self, trained_sleep):
        assert trained_sleep.galaxy_encoder is not None

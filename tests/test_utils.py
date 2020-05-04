import numpy as np
import torch

from celeste.utils import const


class TestUtils:
    def test_get_one_hot(self):
        # This tests the "get_one_hot_encoding_from_int"
        # function. We check that it returns a valid one-hot encoding

        n_classes = 10
        z = torch.randint(0, 10, (100,)).to(const.device)

        z_one_hot = const.get_one_hot_encoding_from_int(z, n_classes)

        assert all(z_one_hot.sum(1) == 1)
        assert all(z_one_hot.float().max(1)[0] == 1)
        assert all(z_one_hot.float().max(1)[1].float() == z.float())

    def test_is_on_from_n_stars(self):
        max_stars = 10
        n_stars = torch.Tensor(np.random.choice(max_stars, 5)).to(const.device)

        is_on = const.get_is_on_from_n_sources(n_stars, max_stars)

        assert torch.all(is_on.sum(1) == n_stars)
        assert torch.all(is_on == is_on.sort(1, descending=True)[0])

    def test_is_on_from_n_stars2d(self):
        n_samples = 5
        batchsize = 3
        max_stars = 10

        n_stars = torch.from_numpy(
            np.random.choice(max_stars, (n_samples, batchsize))
        ).to(const.device)
        is_on = const.get_is_on_from_patch_n_sources_2d(n_stars, max_stars)

        for i in range(n_samples):
            assert torch.all(
                const.get_is_on_from_n_sources(n_stars[i], max_stars) == is_on[i]
            )

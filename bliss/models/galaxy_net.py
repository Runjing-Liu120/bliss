import pytorch_lightning as pl
import matplotlib.pyplot as plt
from omegaconf import DictConfig

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import L1Loss, MSELoss

from bliss.optimizer import get_optimizer

plt.switch_backend("Agg")


def conv_block(c, use_batch=False):
    extra = lambda c: nn.Identity() if not use_batch else nn.BatchNorm2d(c)
    return (
        nn.Conv2d(c, c, 3, padding=1, stride=2),
        extra(c),
        nn.LeakyReLU(),
        nn.Conv2d(c, c * 2, 3, padding=1, stride=1),
        extra(c * 2),
        nn.LeakyReLU(),
    )


def conv_block_transpose(c=8, use_batch=False):
    assert c / 2 % 1 == 0
    extra = lambda c: nn.Identity() if not use_batch else nn.BatchNorm2d(c)
    return (
        nn.ConvTranspose2d(c, c, 3, padding=1, output_padding=1, stride=2),
        extra(c),
        nn.LeakyReLU(),
        nn.ConvTranspose2d(c, c // 2, 3, padding=1, output_padding=1, stride=1),
        extra(c // 2),
        nn.LeakyReLU(),
    )


def get_conv_blocks(mode="dec", sizes=(2, 4, 8), use_batchnorm=False):
    if len(sizes) == 0:
        return (nn.Identity(),)

    if mode == "dec":
        return [b for c in sizes for b in conv_block(c, use_batchnorm)]

    if mode == "enc":
        return [b for c in sizes[::-1] for b in conv_block_transpose(c, use_batchnorm)]

    raise ValueError("mode is not supported.")


class GalaxyEncoder(nn.Module):
    def __init__(
        self, slen=48, latent_dim=8, n_bands=1, hidden=256, start=2, n_sizes=3, use_batchnorm=False
    ):
        super().__init__()
        min_slen = slen / 2 ** (n_sizes + 1)  # e.g. slen = 64, min_slen = 4
        assert min_slen % 1 == 0
        min_slen = int(min_slen)

        sizes = [start * 2 ** i for i in range(n_sizes)]
        end = start * 2 ** n_sizes
        self.features = nn.Sequential(
            nn.Conv2d(n_bands, start, 3, stride=1, padding=1),
            nn.Identity() if not use_batchnorm else nn.BatchNorm2d(start),
            nn.LeakyReLU(),
            *get_conv_blocks(mode="dec", sizes=sizes),
            nn.Conv2d(end, end, 3, padding=1, stride=2),
            nn.Identity() if not use_batchnorm else nn.BatchNorm2d(end),
            nn.LeakyReLU(),
            nn.Flatten(1, -1),
            nn.Linear(end * min_slen ** 2, hidden),
            nn.LeakyReLU(),
            nn.Linear(hidden, latent_dim),
        )

    def forward(self, image):
        return self.features(image)


class GalaxyDecoder(nn.Module):
    def __init__(self, slen=48, latent_dim=8, n_bands=1, hidden=256, start=2, n_sizes=3):
        super().__init__()

        self.slen = slen

        min_slen = slen / 2 ** (n_sizes + 1)  # e.g. slen = 64, min_slen = 4
        assert min_slen % 1 == 0
        self.min_slen = int(min_slen)
        self.end = start * 2 ** n_sizes
        sizes = [start * 2 ** i for i in range(n_sizes)]

        self.fc = nn.Sequential(
            nn.Linear(latent_dim, hidden),
            nn.LeakyReLU(),
            nn.Linear(hidden, self.end * self.min_slen ** 2),
            nn.LeakyReLU(),
        )

        self.deconv = nn.Sequential(
            *get_conv_blocks(mode="enc", sizes=sizes),
            nn.ConvTranspose2d(start, start, 3, padding=1, stride=2, output_padding=1),
            nn.LeakyReLU(),
            nn.ConvTranspose2d(start, n_bands, 3, padding=1, stride=1),
        )

    def forward(self, z):
        z = self.fc(z)
        z = z.view(-1, self.end, self.min_slen, self.min_slen)
        z = self.deconv(z)
        z = z[:, :, : self.slen, : self.slen]
        assert z.shape[-1] == self.slen and z.shape[-2] == self.slen
        recon_mean = F.relu(z)
        return recon_mean


class OneCenteredGalaxy(pl.LightningModule):

    # ---------------
    # Model
    # ----------------

    def __init__(self, cfg: DictConfig):
        super().__init__()
        self.cfg = cfg
        self.save_hyperparameters(cfg)

        self.enc = GalaxyEncoder(**cfg.model.params)
        self.dec = GalaxyDecoder(**cfg.model.params)

        self.warm_up = cfg.model.warm_up
        self.beta = cfg.model.beta
        self.loss_type = cfg.model.loss_type

        assert self.warm_up == 0 or self.beta == 1, "Only one of 'warm_up'/'beta' can be active."

        self.register_buffer("zero", torch.zeros(1))
        self.register_buffer("one", torch.ones(1))
        self.register_buffer("free_bits", torch.tensor([cfg.model.free_bits]))

    def forward(self, image, background):
        # sampling images from the real distribution
        # z | x ~ decoder

        # shape = [nsamples, latent_dim]
        z = self.enc.forward(image - background)

        # reconstructed mean/variances images (per pixel quantities)
        # no background
        recon_mean = self.dec.forward(z)
        recon_mean = recon_mean + background

        return recon_mean

    def get_loss(self, image, recon_mean):

        if self.loss_type == "L2":
            return MSELoss(reduction="sum")(image, recon_mean)

        if self.loss_type == "L1":
            return L1Loss(reduction="sum")(image, recon_mean)

        raise NotImplementedError("Loss not implemented.")

    # ---------------
    # Optimizer
    # ----------------

    def configure_optimizers(self):
        name = self.hparams.optimizer.name
        optim_params = self.hparams.optimizer.params
        return get_optimizer(name, self.parameters(), optim_params)

    # ---------------
    # Training
    # ----------------

    def training_step(self, batch, batch_idx):  # pylint: disable=unused-argument
        images, background = batch["images"], batch["background"]
        recon_mean = self(images, background)
        loss = self.get_loss(images, recon_mean)
        self.log("train_loss", loss)
        return loss

    # ---------------
    # Validation
    # ----------------

    def validation_step(self, batch, batch_idx):  # pylint: disable=unused-argument
        images, background = batch["images"], batch["background"]
        recon_mean = self(images, background)
        loss = self.get_loss(images, recon_mean)

        # metrics
        self.log("val_loss", loss)
        residuals = (images - recon_mean) / torch.sqrt(images)
        self.log("val_max_residual", residuals.abs().max())
        return {"images": images, "recon_mean": recon_mean}

    def validation_epoch_end(self, outputs):
        images = outputs[0]["images"][:10]
        recon_mean = outputs[0]["recon_mean"][:10]
        fig = self.plot_reconstruction(images, recon_mean)
        if self.logger:
            self.logger.experiment.add_figure(f"Images {self.current_epoch}", fig)

    def plot_reconstruction(self, images, recon_mean):
        # only plot i band if available, otherwise the highest band given.
        assert images.size(0) >= 10
        assert self.enc.n_bands == self.dec.n_bands
        n_bands = self.enc.n_bands
        num_examples = 10
        num_cols = 3
        band_idx = min(2, n_bands - 1)
        residuals = (images - recon_mean) / torch.sqrt(images)
        plt.ioff()

        fig = plt.figure(figsize=(10, 25))
        plt.suptitle("Epoch {:d}".format(self.current_epoch))

        for i in range(num_examples):

            plt.subplot(num_examples, num_cols, num_cols * i + 1)
            plt.title("images")
            plt.imshow(images[i, band_idx].data.cpu().numpy())
            plt.colorbar()

            plt.subplot(num_examples, num_cols, num_cols * i + 2)
            plt.title("recon_mean")
            plt.imshow(recon_mean[i, band_idx].data.cpu().numpy())
            plt.colorbar()

            plt.subplot(num_examples, num_cols, num_cols * i + 3)
            plt.title("residuals")
            plt.imshow(residuals[i, band_idx].data.cpu().numpy())
            plt.colorbar()

        plt.tight_layout()

        return fig

    def test_step(self, batch, batch_idx):  # pylint: disable=unused-argument
        images, background = batch["images"], batch["background"]
        recon_mean = self(images, background)
        residuals = (images - recon_mean) / torch.sqrt(images)
        self.log("max_residual", residuals.abs().max())

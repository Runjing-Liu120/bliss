# %%
from pytorch_lightning import Trainer
from torch.utils.data.dataset import Dataset
from bliss.models.fnp import SDSS_HNP
import numpy as np
import torch

from pathlib import Path

import bliss
from bliss.datasets import sdss
from bliss.plotting import plot_image, plot_image_locs, _plot_locs


import matplotlib.pyplot as plt
from sklearn.cluster import KMeans

#%%
class SDSS(Dataset):
    def __init__(self, sdss_source, band=2, min_stars_in_field=100):
        super().__init__()
        self.band = band
        self.sdss_source = [s for s in sdss_source if len(s["prs"]) > min_stars_in_field]
        self.cached_items = [None] * len(self.sdss_source)

    def __len__(self):
        return len(self.cached_items)

    def __getitem__(self, idx):
        if self.cached_items[idx] is None:
            out = self.makeitem(idx)
            self.cached_items[idx] = out
        else:
            out = self.cached_items[idx]
        return out

    def makeitem(self, idx):
        data = self.sdss_source[idx]
        img = data["image"][self.band]
        locs = torch.stack((torch.from_numpy(data["prs"]), torch.from_numpy(data["pts"])), dim=1)
        X = (locs - locs.mean(0)) / locs.std(0)

        ## Randomize order
        idxs = np.random.choice(X.size(0), X.size(0), replace=False)
        X = X[idxs]
        locs = locs[idxs]

        return (X, img, locs)

    @staticmethod
    def make_G_from_clust(c, nclust=None):
        if not nclust:
            nclust = len(np.unique(c))
        G = torch.zeros((len(c), nclust))
        for i in range(nclust):
            G[c == i, i] = 1.0
        return G

    def plot_clustered_locs(self, idx):
        pl, axes = plt.subplots(nrows=1, ncols=1, figsize=(4, 4))
        plot_image(pl, axes, self.sdss_source[idx]["image"][2])
        _, _, locs = self[idx]
        km = KMeans(n_clusters=5)
        clst = km.fit_predict(locs.numpy())
        colors = ["red", "green", "blue", "orange", "yellow"]
        for i, cl in enumerate(np.unique(clst)):
            _plot_locs(axes, 1, 0, locs[clst == cl], color=colors[i], s=3)

        plot_image_locs(axes, 1, 0, km.cluster_centers_, colors=("white",))
        return pl


#%%
sdss_source = sdss.SloanDigitalSkySurvey(
    # sdss_dir=sdss_dir,
    Path(bliss.__file__).parents[1].joinpath("data/sdss_all"),
    run=3900,
    camcol=6,
    # fields=range(300, 1000),
    fields=(808,),
    bands=range(5),
)


#%%
# Pickle
# sdss_dataset_file = Path("sdss_source.pkl")
# if sdss_dataset_file.exists() is False:
sdss_dataset = SDSS(sdss_source)
#     torch.save(sdss_dataset, sdss_dataset_file)
# else:
#     sdss_dataset = torch.load(sdss_dataset_file)

#%%
pl = sdss_dataset.plot_clustered_locs(0)
pl.savefig("clustered_locs.png")

# %%
m = SDSS_HNP(5, 4, sdss_dataset)
trainer = Trainer(max_epochs=400, checkpoint_callback=False, gpus=[4])
# %%
trainer.fit(m)
# %%
torch.save(m.hnp.state_dict(), "star_hnp_state_dict.pt")
# %%
X, img, locs = sdss_dataset[0]
X, S, Y = m.prepare_batch(sdss_dataset[0])
km = KMeans(n_clusters=m.n_clusters)
c = km.fit_predict(locs.cpu().numpy())
G = sdss_dataset.make_G_from_clust(c)
x = m.predict(X, S)
# %%
plt.imshow(x[20].reshape(5, 5))
# %%
plt.imshow(Y[20].reshape(5, 5))
# %%
def plot_cluster_images(c, y_true, y_pred, n_S=0):
    n_max = 0
    for i in np.unique(c):
        if sum(c == i) > n_max:
            n_max = sum(c == i)
    figsize = (2 * n_max * 2, 10)
    plot, axes = plt.subplots(nrows=len(np.unique(c)), ncols=n_max * 2, figsize=figsize)
    in_posterior = np.array([False] * n_S + [True] * (len(c) - n_S))
    for i in np.unique(c):
        ytc = y_true[c == i]
        ypc = y_pred[c == i]
        ipc = in_posterior[c == i]
        for j in range(n_max):
            if j < len(ipc):
                ax = axes[i, 2 * j]
                ax.imshow(ytc[j].reshape(5, 5), interpolation="nearest")
                ax.set_title("real")
                ax.get_xaxis().set_visible(False)
                ax.get_yaxis().set_visible(False)
                ax = axes[i, 2 * j + 1]
                ax.imshow(ypc[j].reshape(5, 5), interpolation="nearest")
                title = "post" if ipc[j] else "recn"
                ax.get_xaxis().set_visible(False)
                ax.get_yaxis().set_visible(False)
                ax.set_title(title)
            else:
                axes[i, 2 * j].axis("off")
                axes[i, 2 * j + 1].axis("off")
    return plot, axes


def plot_cluster_representatives(m, X, S, c, n_show=10, n_samples=100):
    Ys = torch.stack([m.hnp.predict(X, S, mean_Y=True) for i in range(n_samples)])
    n_S = S.size(0)
    figsize = (2 * (n_show + 1), 10)
    plot, axes = plt.subplots(nrows=len(np.unique(c)), ncols=n_show + 1, figsize=figsize)
    in_posterior = np.array([False] * n_S + [True] * (len(c) - n_S))
    for i in np.unique(c):
        idx = np.argmax(np.array(c == i) * in_posterior)
        for j in range(n_show):
            ax = axes[i, j]
            ax.imshow(Ys[j, idx].reshape(5, 5), interpolation="nearest")
            ax.get_xaxis().set_visible(False)
            ax.get_yaxis().set_visible(False)
        ax = axes[i, n_show]
        avg = Ys[:, idx].mean(0)
        ax.imshow(avg.reshape(5, 5), interpolation="nearest")
        ax.get_xaxis().set_visible(False)
        ax.get_yaxis().set_visible(False)
        ax.set_title("Average")

    return plot, axes


def plot_cluster_stars(Y, c, n_show=7):
    figsize = (2 * (n_show + 1), 10)
    plot, axes = plt.subplots(nrows=len(np.unique(c)), ncols=n_show + 1, figsize=figsize)
    for i in np.unique(c):
        Yc = Y[c == i]
        for j in range(n_show):
            ax = axes[i, j]
            ax.imshow(Yc[j].reshape(5, 5), interpolation="nearest")
            ax.get_xaxis().set_visible(False)
            ax.get_yaxis().set_visible(False)
        ax = axes[i, n_show]
        ax.imshow(Yc.mean(0).reshape(5, 5), interpolation="nearest")
        ax.get_xaxis().set_visible(False)
        ax.get_yaxis().set_visible(False)
        ax.set_title("Average")

    return plot, axes


def pred_mean(m, X, G, S, n_samples):
    return torch.stack([m.predict(X, G, S) for i in range(n_samples)]).mean(0)


#%%
xall = pred_mean(m, X, G, S, 100)
p, a = plot_cluster_images(c, Y, x, n_S=Y.size(0))
p.savefig("test.png", transparent=False)
# %%
x20 = pred_mean(m, X, G, S[:20], 100)
p, a = plot_cluster_images(c, Y, x20, n_S=20)
p.savefig("test20.png")
# %%
# No input (just predict from catalog)
x0 = pred_mean(m, X, G, S[:0], 100)
p, a = plot_cluster_images(c, Y, x0, n_S=0)
p.savefig("test0.png")

# %%
# Plot the "cluster" representatives for each star
# With no data (sample from prior)
p, a = plot_cluster_representatives(m, X, S[:0], c)
p.savefig("cluster_reps0.png")
# %%
# With a lot of data
p, a = plot_cluster_representatives(m, X, S[:200], c, 10)
p.savefig("cluster_reps200.png")
#%%
# Plot some random samples from each cluster
p, a = plot_cluster_stars(Y, c, 10)
p.savefig("cluster_stars.png")
# %%

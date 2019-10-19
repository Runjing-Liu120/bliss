import torch

import numpy as np

import simulated_datasets_lib
import inv_kl_objective_lib as inv_kl_lib
from kl_objective_lib import sample_normal, sample_class_weights

from psf_transform_lib import get_psf_transform_loss

import time

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

def run_sleep(star_encoder, loader, optimizer, n_epochs, out_filename, iteration):
    print_every = 10

    test_losses = np.zeros((4, n_epochs // print_every + 1))

    for epoch in range(n_epochs):
        t0 = time.time()

        # draw fresh data
        loader.dataset.set_params_and_images()

        avg_loss, counter_loss, locs_loss, fluxes_loss \
            = inv_kl_lib.eval_star_encoder_loss(star_encoder, loader,
                                                optimizer, train = True)

        elapsed = time.time() - t0
        print('[{}] loss: {:0.4f}; counter loss: {:0.4f}; locs loss: {:0.4f}; fluxes loss: {:0.4f} \t[{:.1f} seconds]'.format(\
                        epoch, avg_loss, counter_loss, locs_loss, fluxes_loss, elapsed))

        if (epoch % print_every) == 0:
            loader.dataset.set_params_and_images()
            _ = inv_kl_lib.eval_star_encoder_loss(star_encoder,
                                                loader, train = True)

            loader.dataset.set_params_and_images()
            test_loss, test_counter_loss, test_locs_loss, test_fluxes_loss = \
                inv_kl_lib.eval_star_encoder_loss(star_encoder,
                                                loader, train = False)

            print('**** test loss: {:.3f}; counter loss: {:.3f}; locs loss: {:.3f}; fluxes loss: {:.3f} ****'.format(\
                test_loss, test_counter_loss, test_locs_loss, test_fluxes_loss))

            outfile = out_filename + '-iter' + str(iteration)
            print("writing the encoder parameters to " + outfile)
            torch.save(star_encoder.state_dict(), outfile)

            test_losses[:, epoch // print_every] = np.array([test_loss, test_counter_loss, test_locs_loss, test_fluxes_loss])
            np.savetxt(out_filename + '-test_losses-' + 'iter' + str(iteration),
                        test_losses)


def sample_star_encoder(star_encoder, full_image, full_background, return_map = False):
    # the image stamps
    image_stamps = star_encoder.get_image_stamps(full_image, locs = None, fluxes = None, trim_images = False)[0]
    background_stamps = star_encoder.get_image_stamps(full_background, locs = None, fluxes = None, trim_images = False)[0]

    # pass through NN
    h = star_encoder._forward_to_last_hidden(image_stamps, background_stamps).detach()
    # get log probs
    log_probs = star_encoder._get_logprobs_from_last_hidden_layer(h)

    # sample number of stars
    if return_map:
        n_stars_sampled = torch.argmax(log_probs, dim = 1)
    else:
        n_stars_sampled = sample_class_weights(torch.exp(log_probs))

    is_on_array = simulated_datasets_lib.get_is_on_from_n_stars(n_stars_sampled, star_encoder.max_detections)

    # get variational parameters
    logit_loc_mean, logit_loc_logvar, \
        log_flux_mean, log_flux_logvar = \
            star_encoder._get_params_from_last_hidden_layer(h, n_stars_sampled)

    if return_map:
        logit_loc_sd = 0.
        log_flux_sd = 0.
    else:
        logit_loc_sd = torch.exp(0.5 * logit_loc_logvar)
        log_flux_sd = torch.exp(0.5 * log_flux_logvar)

    # sample locations
    subimage_locs_sampled = \
        torch.sigmoid(logit_loc_mean + \
                        torch.randn(logit_loc_mean.shape) * logit_loc_sd) * \
                        is_on_array.unsqueeze(2).float()

    # sample fluxes
    subimage_fluxes_sampled = \
        torch.exp(log_flux_mean + \
            torch.randn(log_flux_mean.shape) * log_flux_sd) * \
                is_on_array.float()

    # get parameters on full image
    locs_full_image, fluxes_full_image, n_stars_full = \
        image_utils.get_full_params_from_patch_params(subimage_locs_sampled,
                                                      subimage_fluxes_sampled,
                                                    star_encoder.tile_coords,
                                                    star_encoder.full_slen,
                                                    star_encoder.stamp_slen,
                                                    star_encoder.edge_padding,
                                                    star_encoder.batchsize)

    return locs_full_image, fluxes_full_image, n_stars_full


def run_wake(full_image, full_background, star_encoder, psf_transform, optimizer,
                n_epochs, out_filename, iteration):

    test_losses = np.zeros(n_epochs)
    for epoch in range(n_epochs):
        t0 = time.time(); print_every = 10

        optimizer.zero_grad()

        # sample variational parameters
        sampled_locs_full_image, sampled_fluxes_full_image, sampled_n_stars_full = \
            sample_star_encoder(star_encoder, full_image, full_background,
                                    return_map = True)

        # get loss
        loss = get_psf_loss(full_image, full_background,
                            sampled_locs_full_image,
                            sampled_fluxes_full_image,
                            n_stars = sampled_n_stars_full,
                            psf = psf_transform.forward(),
                            pad = 5)[1]

        avg_loss = loss.mean()

        avg_loss.backward()
        optimizer.step()

        elapsed = time.time() - t0
        print('[{}] loss: {:0.4f} \t[{:.1f} seconds]'.format(\
                    epoch, avg_loss, elapsed))

        test_losses[epoch] = avg_loss
        if (epoch % print_every) == 0:
            outfile = out_filename + '-iter' + str(iteration)
            print("writing the psf parameters to " + outfile)
            torch.save(psf_transform.state_dict(), outfile)

            np.savetxt(out_filename + '-test_losses-' + 'iter' + str(iteration),
                        test_losses)

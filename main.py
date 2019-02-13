"""
This script feeds images from the VOC Pascal 2012 dataset to a convolutional neural network for object detection.
The network is pretrained on the ImageNet ILSVRC dataset, which contains 1000 different object classes.
For each image in the VOC dataset, the network predicts the object class that is most apparent in the image.

This code is based on:
- https://github.com/pytorch/examples/blob/master/imagenet/main.py
- https://heartbeat.fritz.ai/basics-of-image-classification-with-pytorch-2f8973c51864
"""

import torch
import torch.utils.data
import torch.autograd
import torch.cuda
import torchvision.transforms as transforms
import torchvision.models

import matplotlib.pyplot as plt
import numpy as np
import pprint
import os
import requests
import json
from random import randint
from progressbar import ProgressBar
from random import random

from PIL import Image

cuda = torch.cuda.is_available()

method = "rm"

# boo: Black One Out
horizontal_resolution = 10
vertical_resolution = 10

# bonr: Black Out Randomly
nb_frames = 100
black_out_ratio = 0.1

# rm: random masking
nb_masks = 100
mask_probability = 0.7
mask_height_resolution = 50
mask_width_resolution = 50


class CustomDataset(torch.utils.data.dataset.Dataset):

    def __init__(self, transform=None):
        self.transform = transform
        self.img_names = [os.path.join(os.getcwd(), "data/VOCselection/Figure_{}.png".format(nb)) for nb in range(16)]

    def __getitem__(self, index):
        imgg = Image.open(self.img_names[index]).convert('RGB')
        target = 0      # dummy label

        if self.transform is not None:
            imgg = self.transform(imgg)

        return imgg, target

    def __len__(self):
        return len(self.img_names)


def generate_random_mask(height, width, height_res, width_res, mask_probability=0.4):
    """
    See RISE paper section 3.2

    H = height
    h = h
    C_H = height_res

    and analog for width.
    """
    h = height // height_res + 1  # +1 to round up
    w = width // width_res + 1

    # generate a (small) mask with random pixels
    mask_small = np.random.random((h, w)) >= mask_probability
    mask_small = np.array(mask_small, dtype=float)

    # upsample the mask using binlinear interpolation (for smooth edges)
    mask_small_img = Image.fromarray(mask_small)
    mask_large_img = mask_small_img.resize(((w + 1) * width_res, (h + 1) * height_res), resample=Image.BILINEAR)
    mask_large = np.array(mask_large_img)

    # crop areas of size (height, width) with uniformly random indents in range(0, resolution)
    offset = np.random.random(2) * np.array([height_res, width_res])
    offset = np.array(offset, dtype=int)
    height_offset, width_offset = offset
    result = mask_large[height_offset:height + height_offset, width_offset:width + width_offset]
    return torch.tensor(result)


if __name__ == '__main__':

    # ==========
    # == DATA ==
    # ==========
    # Data directories and files. Will be created later on if not yet present.
    voc_dataset_dir = os.path.join(os.getcwd(), "data/VOCdevkit")
    imagenet_index_file = os.path.join(os.getcwd(), "data/imagenet_class_index.json")

    # Download the image dataset if not yet present, and create the data loaders for it.
    # Images must be transformed according to https://pytorch.org/docs/stable/torchvision/models.html
    download_voc_dataset = not os.path.exists(voc_dataset_dir)
    print("Download VOC dataset: {}".format(download_voc_dataset))

    img_normalize_mean = [0.485, 0.456, 0.406]
    img_normalize_std = [0.229, 0.224, 0.225]
    transform = transforms.Compose(
        [transforms.ToTensor(),
         transforms.Normalize(img_normalize_mean, img_normalize_std)])

    # trainset = torchvision.datasets.VOCDetection(root='./data', year='2012', image_set="train",
    #                                              download=download_voc_dataset, transform=transform)
    # trainloader = torch.utils.data.DataLoader(trainset, batch_size=1,
    #                                           shuffle=True, num_workers=2)

    # testset = torchvision.datasets.VOCDetection(root='./data', year='2012', image_set="val",
    #                                             download=download_voc_dataset, transform=transform)
    # testloader = torch.utils.data.DataLoader(testset, batch_size=1,
    #                                          shuffle=False, num_workers=2)

    customset = CustomDataset(transform=transform)
    customloader = torch.utils.data.DataLoader(customset, batch_size=1, shuffle=False, num_workers=2)

    # (Actually, only one of these data loaders is used since the network doesn't have to be trained anymore.)

    # Download the ImageNet class index if not yet present. The class index is used to map indices to their
    # corresponding class name.
    download_imagenet_index = not os.path.exists(imagenet_index_file)
    print("Download ImageNet class index: {}".format(download_imagenet_index))
    if download_imagenet_index:
        data = requests.get('https://s3.amazonaws.com/deep-learning-models/image-models/imagenet_class_index.json')
        with open(imagenet_index_file, "w", encoding="utf-8") as file:
            file.write(data.text)

    class_map = json.load(open(imagenet_index_file))
    # class_map looks as follows:
    #   {"0": ["n01440764", "tench"],
    #    "1": ["n01443537", "goldfish"],
    #    "2": ["n01484850", "great_white_shark"],
    #    ...}


    # ===========
    # == MODEL ==
    # ===========
    # Create the model. All models are pre-trained on ImageNet.
    model_names = sorted(name for name in torchvision.models.__dict__
                         if name.islower() and not name.startswith("__")
                         and callable(torchvision.models.__dict__[name]))
    print("Following models are available: {}".format(model_names))
    model = torchvision.models.resnet50(pretrained=True)
    if cuda:
        model = model.cuda()
    model.eval()
    print("Current model: {}".format(str(model.__class__)))
    print()


    # ===============
    # == UTILITIES ==
    # ===============
    # Opposite of transforms.Normalize
    def unnormalize(img):
        img2 = []
        for channel in range(img.shape[0]):
            img2.append(img[channel] * img_normalize_std[channel] + img_normalize_mean[channel])
        return torch.stack(img2)

    # Function to show an image
    def imshow(img, map=None):

        img = unnormalize(img)

        # scale map to range 0..1
        if map is not None:
            map_normalized = torch.zeros_like(map)
            map_normalized[0] = map[0] / torch.max(map[0])
        if map is None:
            fig, ax1 = plt.subplots(1, 1, figsize=(15, 7))
            ax1.imshow(np.transpose(img.cpu().numpy(), (1, 2, 0)))      # different shape conventions between matplotlib and pytorch
        else:
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 7))
            ax1.imshow(np.transpose(img.cpu().numpy(), (1, 2, 0)))
            ax2.imshow(np.transpose(map_normalized.cpu().numpy(), (1, 2, 0)))

        plt.show()


    # Function to pretty print a (json) dictionary
    pp = pprint.PrettyPrinter(indent=4)
    def pprintdict(dic):
        pp.pprint(dic)


    # Function to map an index to its corresponding object class name
    def index_to_class_name(index):
        return class_map[str(index)][1]


    def predict_class(img, expected_class_index):
        # Send the image though the model to get the object prediction
        predictions = model(torch.autograd.Variable(img))
        # predictions:
        # - Tensor of shape (batch_size, nb_of_classes)
        # - The class with the highest value is the predicted class

        if expected_class_index is None:
            # Find the best prediction for each image in the batch
            max_value, max_index = torch.max(predictions, dim=1)

            # Find the predicted class for each image in the batch.
            # predicted_classes = [index_to_class_name(int(max_index[batch])) for batch in range(predictions.shape[0])]
            class_index = int(max_index[0])
            predicted_class = index_to_class_name(class_index)
            # print("Prediction: " + predicted_class + " with a certainty of " + str(max_value.item()))
            print("Prediction: " + predicted_class)

            return class_index, max_value.item()

        else:
            cert = predictions[0][expected_class_index].item()
            # print("Certainty of " + str(cert))

            return expected_class_index, cert

    def black_out(img, x_low, x_high, y_low, y_high):
        img[0, :, x_low:x_high + 1, y_low:y_high + 1] = 0.
        return img


    def highlight(img, x_low, x_high, y_low, y_high, grad):
        img[0, 0, x_low:x_high + 1, y_low:y_high + 1] += grad
        # img[0, 1:, x_low:x_high + 1, y_low:y_high + 1] = 0


    def predict_blacked_out(image, blocks, res_img, most_likely_index, certainty):
        img = image.clone().detach()

        for (x_block, y_block) in blocks:
            x_lower = int((image.shape[2] + 1) / horizontal_resolution) * x_block
            x_upper = int(((image.shape[2] + 1) / horizontal_resolution)) * (x_block + 1) - 1
            y_lower = int((image.shape[3] + 1) / vertical_resolution) * y_block
            y_upper = int(((image.shape[3] + 1) / vertical_resolution)) * (y_block + 1) - 1

            black_out(img, x_lower, x_upper, y_lower, y_upper)

        _, cert = predict_class(img, most_likely_index)

        for (x_block, y_block) in blocks:
            x_lower = int((image.shape[2] + 1) / horizontal_resolution) * x_block
            x_upper = int(((image.shape[2] + 1) / horizontal_resolution)) * (x_block + 1) - 1
            y_lower = int((image.shape[3] + 1) / vertical_resolution) * y_block
            y_upper = int(((image.shape[3] + 1) / vertical_resolution)) * (y_block + 1) - 1

            highlight(res_img, x_lower, x_upper, y_lower, y_upper, max(certainty - cert, 0.))

    # =============================
    # == ACTUAL OBJECT DETECTION ==
    # =============================
    # Press q or close the image window to continue to the next image.
    #
    # The image- and label variables may be batches of images and labels. The batch size is defined in the DataLoader.
    for i, (image, label) in enumerate(customloader):

        print(" --- Image {} ---".format(i))
        if cuda:
            image = image.cuda()

        if image.shape[2] < 224 or image.shape[3] < 224:
            print(" -> Image too small, skipped.")
            continue

        # Original image
        most_likely_index, certainty = predict_class(image, None)

        result_image = torch.zeros(image.shape)

        if method == 'boo':
            pbar = ProgressBar()
            for x_block in pbar(range(horizontal_resolution)):
                for y_block in range(vertical_resolution):
                    predict_blacked_out(image, [(x_block, y_block)], result_image, most_likely_index, certainty)

            imshow(torchvision.utils.make_grid(image), torchvision.utils.make_grid(result_image))

        elif method == 'bonr':
            pbar = ProgressBar()
            for frame in pbar(range(nb_frames)):
                blocks = []
                for block in range(int(horizontal_resolution*vertical_resolution)):
                    if random() > black_out_ratio:
                        continue
                    x_block = randint(0, horizontal_resolution - 1)
                    y_block = randint(0, vertical_resolution - 1)
                    blocks.append((x_block, y_block))
                predict_blacked_out(image, blocks, result_image, most_likely_index, certainty)

            imshow(torchvision.utils.make_grid(image), torchvision.utils.make_grid(result_image))

        elif method == "rm":
            height, width = image.shape[2:]
            pbar = ProgressBar()
            for mask_idx in pbar(range(nb_masks)):

                mask = generate_random_mask(height, width, mask_height_resolution, mask_width_resolution,
                                            mask_probability)
                mask_img = torch.stack(tuple([mask] * 3))[None, ...]    # reshape the mask to (1, 3, height, width)
                img_masked = image * mask_img

                # imshow(torchvision.utils.make_grid(img_masked))

                _, mask_weight = predict_class(img_masked, most_likely_index)

                result_image = result_image + (mask_img * mask_weight)
                #
                # print(mask_weight)
                # print("max result value: {}".format(torch.max(result_image)))
                #
                # imshow(torchvision.utils.make_grid(img_masked), torchvision.utils.make_grid(result_image))

            imshow(torchvision.utils.make_grid(image), torchvision.utils.make_grid(result_image))
        else:
            print("No method '" + method + "' available.")

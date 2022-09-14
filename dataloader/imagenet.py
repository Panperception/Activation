import os

import torch
from PIL import Image
import h5py
import torch.utils.data as data
from torch.utils.data import Dataset
from torchvision.transforms import *

from config import *

IMAGENET_MEAN_STD = [(0.485, 0.456, 0.406), (0.229, 0.224, 0.225)]


class H5PYImageNet(Dataset):
    def __init__(self, file_dir, transform, mode='train'):

        self.dataset = None
        self.transform = transform
        if mode == 'train':
            self.file_path = os.path.join(file_dir, 'train.hdf5')
        else:
            self.file_path = os.path.join(file_dir, 'val.hdf5')

        self.f = h5py.File(self.file_path, 'r')

    def __getitem__(self, index: int):
        sample = self.f['data'][index]
        sample = torch.Tensor(sample)
        sample = self.transform(sample)
        target = self.f['label'][index]
        return sample, target

    def __len__(self):
        return len(self.f['label'])


def get_dataset(args):
    data_dir = os.path.join(DATA_PATH, 'ImageNet')
    # train_dir = os.path.join(data_dir, 'train')
    # val_dir = os.path.join(data_dir, 'train')
    train_transform = Compose([
        # Resize(args.DATA.img_size),
        RandomResizedCrop((224, 224)),
        RandomHorizontalFlip(), ToTensor()])
    val_transform = Compose([
        # Resize(args.DATA.img_size),
        CenterCrop((224, 224)), transforms.ToTensor()])
    train_dataset = H5PYImageNet(data_dir, mode='val', transform=train_transform)
    test_dataset = H5PYImageNet(data_dir, mode='val', transform=val_transform)
    return train_dataset, test_dataset


def get_loaders(args):
    train_dataset, test_dataset = get_dataset(args)
    train_loader = data.DataLoader(
        dataset=train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        pin_memory=True,
        num_workers=8
    )

    test_loader = data.DataLoader(
        dataset=test_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        pin_memory=True,
    )
    return train_loader, test_loader

# class ValLoader(datasets.Dataset):
#
#     def __init__(self, csv_file, root_dir, transform=None):
#         """
#         Args:
#             csv_file (string): Path to the csv file with annotations.
#             root_dir (string): Directory with all the images.
#             transform (callable, optional): Optional transform to be applied
#                 on a sample.
#         """
#         self.landmarks_frame = pd.read_csv(csv_file)
#         self.root_dir = root_dir
#         self.transform = transform
#
#     def __len__(self):
#         return len(self.landmarks_frame)
#
#     def __getitem__(self, idx):
#         if torch.is_tensor(idx):
#             idx = idx.tolist()
#
#         img_name = os.path.join(self.root_dir,
#                                 self.landmarks_frame.iloc[idx, 0])
#         image = io.imread(img_name)
#         landmarks = self.landmarks_frame.iloc[idx, 1:]
#         landmarks = np.array([landmarks])
#         landmarks = landmarks.astype('float').reshape(-1, 2)
#         sample = {'image': image, 'landmarks': landmarks}
#
#         if self.transform:
#             sample = self.transform(sample)
#
#         return sample

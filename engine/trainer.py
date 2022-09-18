import torch.utils.data as data
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data.distributed import DistributedSampler

from dataloader.base import *
from engine.logger import Log
from models import *


# DDP version
class BaseTrainer:
    def __init__(self, args, rank=-1):
        self.args = args
        self.rank = rank
        self._init_dataset()

        self.model = build_model(args)
        self.start_epoch, self.best_acc = self.resume()
        dist.barrier()

        self.model.cuda(rank)
        self.model = DDP(self.model, device_ids=[rank], output_device=rank)

        self._init_functions()

        self.time_metric = MetricLogger()
        self.metrics = MetricLogger()
        self.result = {'train': dict(), 'test': dict()}
        self.logger = Log(self.args)
        if self.rank == 0:
            self.logger.hello_logger()

    def train_step(self, images, labels):
        images, labels = images.to(self.rank), labels.to(self.rank)
        outputs = self.model(images)
        loss = self.loss_function(outputs, labels)

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        self.lr_scheduler.step()

        top1, top5 = accuracy(outputs, labels)
        self.metrics.update(
            top1=(top1, len(images)), top5=(top5, len(images)),
            loss=(loss, len(images)),
            lr=(self.get_lr(), 1)
        )
        self.metrics.all_reduce()

    # def warmup(self):
    #     if self.args.warmup_steps == 0:
    #         return
    #     loader = InfiniteLoader(self.train_loader)
    #     self.lr_scheduler = warmup_scheduler(self.args, self.optimizer)
    #     for cur_step in range(self.args.warmup_steps):
    #         images, labels = next(loader)
    #         images, labels = to_device(self.args.devices[0], images, labels)
    #         # self.train_step(images, labels)
    #         if cur_step % self.args.print_every == 0 and cur_step != 0 and self.rank == 0:
    #             self.logger.step_logging(cur_step, self.args.warmup_steps, -1, -1, self.metrics, loader.metric)
    #
    #         if cur_step >= self.args.warmup_steps:
    #             break
    #     self.logger.train_logging(-1, self.args.num_epoch, self.metrics, loader.metric)
    #     self.validate_epoch()
    #     self.optimizer = init_optimizer(self.args, self.model)
    #     self.lr_scheduler = init_scheduler(self.args, self.optimizer)
    #
    #     return

    def train_epoch(self, epoch):
        cur_time = time.time()
        for step, (images, labels) in enumerate(self.train_loader):
            data_time = time.time() - cur_time

            images, labels = images.to(self.rank), labels.to(self.rank)
            self.train_step(images, labels)
            if step % self.args.print_every == 0 and step != 0 and self.rank == 0:
                self.logger.step_logging(step, self.args.epoch_step, epoch, self.args.num_epoch,
                                         self.metrics, self.time_metric)

            iter_time = time.time() - cur_time
            cur_time = time.time()
            self.time_metric.update(iter_time=(iter_time, 1), data_time=(data_time, 1))
            self.time_metric.all_reduce()
            self.metrics.all_reduce()
        if self.rank == 0:
            self.logger.train_logging(epoch, self.args.num_epoch, self.metrics, self.time_metric)
        self.time_metric.reset()
        return

    def validate_epoch(self):
        start = time.time()
        self.model.eval()
        for images, labels in self.test_loader:
            images, labels = images.to(self.rank), labels.to(self.rank)
            pred = self.model(images)
            top1, top5 = accuracy(pred, labels)
            self.metrics.update(top1=(top1, len(images)))
            self.metrics.all_reduce()
            # if self.args.record_lip:
            #     self.record_lip(images, labels, pred)
        self.logger.val_logging(self.metrics, time.time() - start)

        self.model.train()
        return self.metrics.meters['top1'].global_avg

    def train_model(self):

        # self.warmup()

        for epoch in range(self.start_epoch, self.args.num_epoch):
            self.train_epoch(epoch)
            self.record_result(epoch)

            acc = self.validate_epoch()
            if acc > self.best_acc:
                self.best_acc = acc
                if self.rank == 0:
                    self.save_ckpt(epoch + 1, self.best_acc, 'best')

        if self.rank == 0:
            if self.args.save_name == '':
                self.args.save_name = str(self.args.num_epoch).zfill(3)
            self.save_result(self.args.model_dir, 'epoch_{}'.format(self.args.save_name))
            self.save_ckpt(self.args.num_epoch, self.best_acc, 'epoch_{}'.format(self.args.save_name))

    def _init_dataset(self):
        train_dataset, test_dataset = set_data_set(self.args)
        self.train_sampler = DistributedSampler(train_dataset, shuffle=False)
        self.test_sampler = DistributedSampler(test_dataset, shuffle=False)
        self.train_loader = data.DataLoader(
            dataset=train_dataset,
            batch_size=self.args.batch_size,
            sampler=self.train_sampler

        )
        self.test_loader = data.DataLoader(
            dataset=test_dataset,
            batch_size=self.args.batch_size,
            sampler=self.test_sampler
        )

        self.args.epoch_step = len(self.train_loader)
        self.args.total_step = self.args.num_epoch * self.args.epoch_step
        return

    def save_ckpt(self, cur_epoch, best_acc=0, name=None):
        ckpt = {
            'epoch': cur_epoch,
            'model_state_dict': self.model.module.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'best_acc': best_acc
        }
        if not name:
            ckpt_path = os.path.join(self.args.model_dir, 'ckpt.pth')
        else:
            ckpt_path = os.path.join(self.args.model_dir, 'ckpt_{}.pth'.format(name))
        torch.save(ckpt, ckpt_path)
        return

    def resume(self):
        if not self.args.resume:
            return 0, 0
        ckpt_path = os.path.join(self.args.model_dir, 'ckpt_{}.pth'.format(self.args.resume_name))
        self.logger.info('Trying to load CKPT from {0}'.format(ckpt_path))
        print('Trying to load CKPT from {0}'.format(ckpt_path))
        try:
            ckpt = torch.load(ckpt_path)
        except FileNotFoundError:
            self.logger.info('CKPT not found, start from Epoch 0')
            print('CKPT not found, start from Epoch 0')
            return 0, 0
        self.model.load_state_dict(ckpt['model_state_dict'])
        self.logger.info('Loading Finished')
        print('Loading Finished')
        if self.args.resume_name == 'best':
            return ckpt['epoch'], ckpt['best_acc']
        else:
            return 0, ckpt['best_acc']

    def save_result(self, path, name=None):
        if not name:
            res_path = os.path.join(path, 'result')
        else:
            res_path = os.path.join(path, 'result_{}'.format(name))
        np.save(res_path, self.result)

    def record_result(self, epoch, mode='train'):

        epoch_result = {}
        for k, v in self.metrics.meters.items():
            epoch_result[k] = v.to_dict()
        self.result[mode][epoch] = epoch_result
        self.metrics.reset()
        return

    @property
    def trained_ratio(self):
        return self.lr_scheduler.last_epoch / self.args.total_step

    def get_lr(self):
        return self.optimizer.param_groups[0]['lr']

    # def record_lip(self, images, labels, outputs):
    #     perturbation = self.lip.attack(images, labels)
    #     local_lip = (self.model(images + perturbation) - outputs)
    #     lip_li = (local_lip.norm(p=float('inf'), dim=1) / perturbation.norm(p=float('inf'), dim=(1, 2, 3))).mean()
    #     lip_l2 = (local_lip.norm(p=2, dim=1) / perturbation.norm(p=2, dim=(1, 2, 3))).mean()
    #     self.update_metric(lip_li=(lip_li, len(images)), lip_l2=(lip_l2, len(images)))
    #     return

    def _init_functions(self):
        self.optimizer = init_optimizer(self.args, self.model)
        self.lr_scheduler = init_scheduler(self.args, self.optimizer)

        self.loss_function = init_loss(self.args)

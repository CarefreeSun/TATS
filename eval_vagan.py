# Copyright (c) Meta Platforms, Inc. All Rights Reserved

import os
import argparse
import pytorch_lightning as pl
from tats import VQGANVisionAction, AverageMeter, get_image_action_dataloader
import torch
from PIL import Image
from tqdm import tqdm
import json
# from tats.dataloader_img import get_image_dataloader


def main():
    pl.seed_everything(42)

    parser = argparse.ArgumentParser()

    # trainer args
    parser.add_argument("--nodes", type=int, default=1, help="nodes")
    parser.add_argument("--devices", type=int, default=8, help="e.g., gpu number")
    parser.add_argument("--default_root_dir", type=str, default="logs/debug")
    parser.add_argument("--max_steps", type=int, default=300000, help="max_steps")
    parser.add_argument("--resume_from_checkpoint", type=str, default=None, help="resume from checkpoint")

    # model args
    parser.add_argument('--embedding_dim', type=int, default=256)
    parser.add_argument('--n_codes', type=int, default=16384)
    parser.add_argument('--n_hiddens', type=int, default=32)
    parser.add_argument('--lr', type=float, default=5e-6)
    parser.add_argument('--downsample', nargs='+', type=int, default=(2, 16, 16))
    parser.add_argument('--disc_channels', type=int, default=64)
    parser.add_argument('--disc_layers', type=int, default=3)
    parser.add_argument('--discriminator_iter_start', type=int, default=5000)
    parser.add_argument('--disc_loss_type', type=str, default='hinge', choices=['hinge', 'vanilla'])
    parser.add_argument('--image_gan_weight', type=float, default=0.2)
    parser.add_argument('--video_gan_weight', type=float, default=0.2)
    parser.add_argument('--l1_weight', type=float, default=1.0)
    parser.add_argument('--l1_action_weight', type=float, default=1.0)
    parser.add_argument('--gan_feat_weight', type=float, default=4.0)
    parser.add_argument('--perceptual_weight', type=float, default=1.0)
    parser.add_argument('--i3d_feat', action='store_true')
    parser.add_argument('--restart_thres', type=float, default=1.0)
    parser.add_argument('--no_random_restart', action='store_true')
    parser.add_argument('--norm_type', type=str, default='batch', choices=['batch', 'group'])
    parser.add_argument('--padding_type', type=str, default='replicate', choices=['replicate', 'constant', 'reflect', 'circular'])
    parser.add_argument('--action_dim', nargs='+', type=int, default=(1, 1, 1, 1, 1, 1, 1), help='number of action dimention, xyz, rpy, gripper')
    parser.add_argument('--action_activation', nargs='+', type=str, default=('none', 'none', 'none', 'none', 'none', 'none', 'sigmoid'), help='activation function for action')
    parser.add_argument('--action_hidden_dim', type=int, default=128, help='hidden dimention of action')
    parser.add_argument('--video_action_layers', type=int, default=12, help='number of action layers')
    parser.add_argument('--action_mask', action='store_true', help='mask action')
    parser.add_argument('--action_mask_ratio', type=float, default=0.1, help='mask ratio for action')
    parser.add_argument('--wo_transformer_residual', action='store_true', help='use transformer residual')

    # data args
    parser.add_argument("--data_root", type=str, default="../robot_datasets/tokenizer-training")
    parser.add_argument("--dataset_names", nargs='+', type=str, 
                        default=("bridge2", 
                                "rt1"
                                ))
    parser.add_argument("--image_root", nargs='+', type=str, 
                        default=("/mnt/robotdata/bridge2/images_bridge",
                                "/mnt/robotdata/RT1/RT1-images"
                                ))
    parser.add_argument("--normalize", action="store_true", help="normalize the actions")
    parser.add_argument("--sequence_length", type=int, default=6)
    parser.add_argument("--batch_size", type=int, default=1)
    parser.add_argument("--num_workers", type=int, default=1)
    parser.add_argument("--resolution", type=int, default=256)
    parser.add_argument('--image_channels', type=int, default=3)
    parser.add_argument('--val_check_interval', type=int, default=1.0)
    parser.add_argument('--log_interval', type=int, default=20)
    parser.add_argument('--save_step_frequency', type=int, default=5000)
    parser.add_argument('--weight_path', type=str, default='/mnt/data-rundong/VQ3D-vision-action/0531-action111-bridge-noMask-woResidual/checkpoints/step_checkpoint-step_30000.ckpt')

    parser.add_argument('--gpu_id', type=int, default=0)

    args = parser.parse_args()

    try:
        print('MASTER_ADDR', os.environ['MASTER_ADDR'])
        print('MASTER_PORT', os.environ['MASTER_PORT'])
        print('NODE_RANK', os.environ['NODE_RANK'])
        print('LOCAL_RANK', os.environ['LOCAL_RANK'])
        print('RANK', os.environ['RANK'])
        print('WORLD_SIZE', os.environ['WORLD_SIZE'])
    except:
        pass

    assert args.normalize and args.wo_transformer_residual

    test_dataloader = get_image_action_dataloader(args, split='test', action=True, return_mean_std=True)

    device = f'cuda:{args.gpu_id}'

    model = VQGANVisionAction(args).to(device)

    image_recon_meter, action_recon_meter, perceptual_meter = AverageMeter(), AverageMeter(), AverageMeter()

    action_dim_wise_meter = [AverageMeter() for _ in range(7)]
    action_dim_wise_normalized_meter = [AverageMeter() for _ in range(7)]

    # load the most recent checkpoint file
    
    assert os.path.exists(args.weight_path)
    ckpt = torch.load(args.weight_path, map_location='cpu')
    model.load_state_dict(ckpt['state_dict'])
    model.eval()

    # mean_std_path = '../data-rundong/robot_datasets/tokenizer-training/bridge2/mean_std.json'
    # mean, std = json.load(open(mean_std_path, 'r'))['mean'], json.load(open(mean_std_path, 'r'))['std']

    os.makedirs(args.default_root_dir, exist_ok=True)
    
    with torch.no_grad():
        for i, data in tqdm(enumerate(test_dataloader)):
            input_video = data['video'].to(device)
            input_action = data['actions'].to(device)
            bsz = input_video.shape[0]
            recon_loss, recon_loss_action, x_recon, x_recon_action, vq_output, vq_output_action, perceptual_loss = model(input_video, input_action)

            image_recon_meter.update(recon_loss.item(), bsz)
            action_recon_meter.update(recon_loss_action.item(), bsz)
            perceptual_meter.update(perceptual_loss.item(), bsz)

            mean, std = data['mean'].to(device).squeeze(), data['std'].to(device).squeeze()

            for j in range(7):
                action_dim_wise_meter[j].update(torch.abs(input_action[..., j] - x_recon_action[..., j]).mean().item(), bsz)
                action_dim_wise_normalized_meter[j].update(torch.abs((input_action[..., j] * std[j] + mean[j]) - (x_recon_action[..., j] * std[j] + mean[j])).mean().item(), bsz)

            # save the x_recon
            # for j in range(x_recon.shape[0]):
            #     img = x_recon[j][:,0].detach().cpu().numpy().transpose(1, 2, 0)
            #     img = (img + 0.5) * 255
            #     img = img.astype('uint8')
            #     img = Image.fromarray(img)
            #     img.save(os.path.join(args.default_root_dir, f'recon_{i}_{j}.png'))

            #     img_gt = input_video[j][:,0].detach().cpu().numpy().transpose(1, 2, 0)
            #     img_gt = (img_gt + 0.5) * 255
            #     img_gt = img_gt.astype('uint8')
            #     img_gt = Image.fromarray(img_gt)
            #     img_gt.save(os.path.join(args.default_root_dir, f'gt_{i}_{j}.png'))

            if i % args.log_interval == 0:
                print(f'[{i}/{len(test_dataloader)}] Image Recon Loss: {image_recon_meter.avg:.4f} Action Recon Loss: {action_recon_meter.avg:.4f} Perceptual Loss: {perceptual_meter.avg:.4f}')
                print(f'Normalized Image Recon Loss: {image_recon_meter.avg:.4f} Normalized Action Recon Loss: {action_recon_meter.avg:.4f} Perceptual Loss: {perceptual_meter.avg:.4f}')
                for j in range(7):
                    print(f'Action Dim {j} Recon Loss: {action_dim_wise_meter[j].avg:.4f}')
                    print(f'Normalized Action Dim {j} Recon Loss: {action_dim_wise_normalized_meter[j].avg:.4f}')

        print(f'Final Image Recon Loss: {image_recon_meter.avg:.4f} Action Recon Loss: {action_recon_meter.avg:.4f} Perceptual Loss: {perceptual_meter.avg:.4f}')
        print(f'Normalized Image Recon Loss: {image_recon_meter.avg:.4f} Normalized Action Recon Loss: {action_recon_meter.avg:.4f} Perceptual Loss: {perceptual_meter.avg:.4f}')
        for j in range(7):
            print(f'Action Dim {j} Recon Loss: {action_dim_wise_meter[j].avg:.4f}')
            print(f'Normalized Action Dim {j} Recon Loss: {action_dim_wise_normalized_meter[j].avg:.4f}')

if __name__ == "__main__":
    main()

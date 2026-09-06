import csv
import os
import time
from argparse import ArgumentParser
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from skimage import __version__ as skimage_version
from skimage.metrics import peak_signal_noise_ratio, structural_similarity
import torchvision.transforms.functional as TF

from image_protocol import USBNet_LUMA_PROTOCOL
from network.network import GraRED_HP3


LUMA_PROTOCOL = USBNet_LUMA_PROTOCOL


def parse_args():
    parser = ArgumentParser(description='MambaV-DUN V3 USBNet-aligned test')
    parser.add_argument('--sample_ratio', type=float, required=True)
    parser.add_argument(
        '--measurement_rounding', choices=('ceil', 'floor', 'round'),
        default='ceil',
        help='Must match training; ceil makes 1%% equal to 11/1024.',
    )
    parser.add_argument('--dataDir', type=str, default='../DataSets/testSets/')
    parser.add_argument('--dataset', type=str,
                        default='Set11,Set14,Urban100,BSD100')
    parser.add_argument('--model_path', type=str, default=None)
    parser.add_argument('--gpu_id', type=str, default='0')
    parser.add_argument('--experiment', type=str, default='v3_usb_aligned')
    parser.add_argument('--mamba_model', type=str, default=None,
                        help='Deprecated compatibility option; V3 uses MambaVision-T.')
    parser.add_argument(
        '--padding', choices=('zero', 'reflect'), default='zero',
        help='Boundary padding. zero is the comparable public-code default; '
             'reflect is retained only for an explicit ablation.',
    )
    parser.add_argument(
        '--save', action='store_true',
        help='Save reconstructed PNG files. Metrics and CSV are always saved.',
    )
    parser.add_argument('--warmup', type=int, default=5,
                        help='Untimed warm-up forwards per dataset')
    return parser.parse_args()


def image_padding(image, block_size=32, mode='zero'):
    _, _, height, width = image.shape
    pad_h = (-height) % block_size
    pad_w = (-width) % block_size
    if not (pad_h or pad_w):
        return image, height, width
    if mode == 'zero':
        padded = F.pad(image, (0, pad_w, 0, pad_h), mode='constant', value=0.0)
    else:
        safe_mode = 'reflect' if height > pad_h and width > pad_w else 'replicate'
        padded = F.pad(image, (0, pad_w, 0, pad_h), mode=safe_mode)
    return padded, height, width


def process_img(path):
    # Match USBNet test.py exactly: OpenCV BGR -> full-range YCrCb Y.
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    y = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)[:, :, 0]
    return TF.to_tensor(y).unsqueeze(0), y.astype(np.float64)


def safe_torch_load(path, map_location):
    try:
        return torch.load(path, map_location=map_location, weights_only=False)
    except TypeError:
        return torch.load(path, map_location=map_location)


def load_checkpoint(model, path):
    checkpoint = safe_torch_load(path, map_location='cpu')
    expected = getattr(model, 'checkpoint_version', 1)
    found = checkpoint.get('checkpoint_version', 1)
    if found != expected:
        raise RuntimeError(
            'checkpoint 版本不兼容: 文件为 V%d，当前模型为 V%d；'
            'full-range V3 需要重新训练。' % (found, expected)
        )
    expected_protocol = getattr(model, 'image_protocol', None)
    found_protocol = checkpoint.get('image_protocol')
    if found_protocol != expected_protocol:
        raise RuntimeError(
            'checkpoint 图像协议不兼容: 文件为 %r，当前要求 %r。'
            % (found_protocol, expected_protocol)
        )
    expected_architecture = getattr(model, 'architecture_name', None)
    found_architecture = checkpoint.get('architecture_name')
    if found_architecture != expected_architecture:
        raise RuntimeError(
            'checkpoint 网络结构不兼容: 文件为 %r，当前要求 %r。'
            % (found_architecture, expected_architecture)
        )
    for key in ('measurement_count', 'measurement_rounding',
                'n_iteration', 'block_size', 'channel'):
        expected_value = getattr(model, key)
        if checkpoint.get(key) != expected_value:
            raise RuntimeError(
                'checkpoint %s 不兼容: 文件为 %r，当前要求 %r。'
                % (key, checkpoint.get(key), expected_value)
            )
    state_dict = checkpoint.get('model', checkpoint)
    if state_dict and next(iter(state_dict)).startswith('module.'):
        state_dict = {key[len('module.'):]: value
                      for key, value in state_dict.items()}
    model.load_state_dict(state_dict, strict=True)
    return checkpoint


def synchronize(device):
    if device.type == 'cuda':
        torch.cuda.synchronize(device)


def list_images(directory):
    extensions = {
        '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp',
        '.ppm', '.pgm', '.webp',
    }
    path = Path(directory)
    if not path.is_dir():
        return []
    return sorted(item for item in path.iterdir()
                  if item.is_file() and item.suffix.lower() in extensions)


def main():
    args = parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu_id
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    model_path = args.model_path or os.path.join(
        '.', 'results', str(args.sample_ratio), args.experiment,
        'model', 'checkpoint-best.pth'
    )
    model = GraRED_HP3(
        ratio=args.sample_ratio,
        measurement_rounding=args.measurement_rounding,
        load_imagenet_weights=False,
    )
    checkpoint = load_checkpoint(model, model_path)
    model.to(device).eval()

    print('checkpoint: %s (epoch=%s, best_epoch=%s, PSNR=%s)' % (
        os.path.abspath(model_path),
        checkpoint.get('epoch'),
        checkpoint.get('best_epoch'),
        checkpoint.get('psnr'),
    ))
    print('请求采样率: %.4f%%' % (100.0 * args.sample_ratio))
    print('实际采样率: %d/1024 = %.4f%% (%s)' % (
        model.measurement_count, 100.0 * model.actual_ratio,
        model.measurement_rounding,
    ))
    print('灰度协议: OpenCV BGR2YCrCb full-range Y [0,255]（USBNet）')
    print('边界填充: %s; 保存重建图: %s' % (args.padding, args.save))

    result_root = os.path.join(
        '.', 'results', str(args.sample_ratio),
        args.experiment,
        'test_results_usbnet_full_%s_float' % args.padding,
    )
    os.makedirs(result_root, exist_ok=True)
    summary = {}

    with torch.no_grad():
        for dataset_name in (name.strip() for name in args.dataset.split(',')):
            if not dataset_name:
                continue
            image_paths = list_images(os.path.join(args.dataDir, dataset_name))
            if not image_paths:
                print('[warning] %s 中没有找到测试图片，已跳过。' % dataset_name)
                continue

            result_dir = os.path.join(result_root, dataset_name)
            os.makedirs(result_dir, exist_ok=True)
            image_dir = os.path.join(result_dir, 'images')
            if args.save:
                os.makedirs(image_dir, exist_ok=True)

            rows = []
            print('\n---> Evaluating %s (%d images) <---' % (
                dataset_name, len(image_paths)
            ))
            warmed_up = False
            for index, image_path in enumerate(image_paths, start=1):
                processed = process_img(image_path)
                if processed is None:
                    print('[warning] 无法读取 %s，已跳过。' % image_path)
                    continue
                image, gt_255 = processed
                padded, raw_h, raw_w = image_padding(
                    image, block_size=model.block_size, mode=args.padding
                )
                padded = padded.to(device)

                if not warmed_up:
                    for _ in range(max(args.warmup, 0)):
                        model(padded)
                    synchronize(device)
                    warmed_up = True

                synchronize(device)
                start = time.perf_counter()
                prediction = model(padded)
                synchronize(device)
                elapsed = time.perf_counter() - start

                pred_255 = (
                    prediction[0, 0, :raw_h, :raw_w]
                    .clamp(0.0, 1.0).cpu().numpy().astype(np.float64) * 255.0
                )
                rec_psnr = peak_signal_noise_ratio(
                    gt_255, pred_255, data_range=255.0
                )
                rec_ssim = structural_similarity(
                    gt_255, pred_255, data_range=255.0
                )
                rows.append((image_path.name, rec_psnr, rec_ssim, elapsed))
                print('[%03d/%03d] %s: %.3f dB, %.5f, %.4fs' % (
                    index, len(image_paths), image_path.name,
                    rec_psnr, rec_ssim, elapsed,
                ))

                if args.save:
                    output = np.rint(pred_255).astype(np.uint8)
                    Image.fromarray(output).save(
                        os.path.join(image_dir, image_path.stem + '.png')
                    )

            if not rows:
                continue
            avg_psnr = float(np.mean([row[1] for row in rows]))
            avg_ssim = float(np.mean([row[2] for row in rows]))
            avg_time = float(np.mean([row[3] for row in rows]))
            summary[dataset_name] = (
                avg_psnr, avg_ssim, avg_time, len(rows)
            )

            csv_path = os.path.join(result_dir, 'results.csv')
            with open(csv_path, 'w', newline='', encoding='utf-8') as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(('image', 'psnr_db', 'ssim', 'time_seconds'))
                writer.writerows(rows)
                writer.writerow(('average', avg_psnr, avg_ssim, avg_time))
            print('[%s] Average: %.3f dB, %.5f, %.4fs' % (
                dataset_name, avg_psnr, avg_ssim, avg_time
            ))

    summary_path = os.path.join(result_root, 'final_summary.txt')
    with open(summary_path, 'w', encoding='utf-8') as summary_file:
        ratio_line = 'Actual CS ratio: %d/1024 = %.4f%% (%s)' % (
            model.measurement_count, 100.0 * model.actual_ratio,
            model.measurement_rounding,
        )
        print('\n' + ratio_line)
        summary_file.write(ratio_line + '\n')
        summary_file.write('Checkpoint: %s; epoch=%s; best_epoch=%s\n' % (
            os.path.abspath(model_path),
            checkpoint.get('epoch'),
            checkpoint.get('best_epoch'),
        ))
        summary_file.write('Architecture: %s; OpenCV=%s; scikit-image=%s\n' % (
            model.architecture_name, cv2.__version__, skimage_version
        ))
        summary_file.write(
            'Luma: %s; padding: %s; metric domain: float [0,255]\n'
            % (LUMA_PROTOCOL, args.padding)
        )
        for name, (avg_psnr, avg_ssim, avg_time, count) in summary.items():
            line = '%-15s | %8.3f dB | %.5f | %.4fs | n=%d' % (
                name, avg_psnr, avg_ssim, avg_time, count
            )
            print(line)
            summary_file.write(line + '\n')
    print('汇总结果: %s' % summary_path)


if __name__ == '__main__':
    main()

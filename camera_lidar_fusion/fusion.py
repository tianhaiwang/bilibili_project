import sys

import cv2
import numpy as np
import matplotlib.pyplot as plt

import pyqtgraph.opengl as gl
from PyQt5.QtWidgets import QApplication


cmap = plt.cm.jet


def read_bin(bin_path, intensity=False):
    """
    读取kitti bin格式文件点云
    :param bin_path:   点云路径
    :param intensity:  是否要强度
    :return:           numpy.ndarray `N x 3` or `N x 4`
    """
    lidar_points = np.fromfile(bin_path, dtype=np.float32).reshape((-1, 4))
    if not intensity:
        lidar_points = lidar_points[:, :3]
    return lidar_points


def read_calib(calib_path):
    """
    读取kitti数据集标定文件
    下载的彩色图像是左边相机的图像, 所以要用P2
    extrinsic = np.matmul(R0, lidar2camera)
    intrinsic = P2
    P中包含第i个相机到0号摄像头的距离偏移(x方向)
    extrinsic变换后的点云是投影到编号为0的相机(参考相机)坐标系中并修正后的点
    intrinsic(P2)变换后可以投影到左边相机图像上
    P0, P1, P2, P3分别代表左边灰度相机，右边灰度相机，左边彩色相机，右边彩色相机
    :return: P0-P3 numpy.ndarray           `3 x 4`
             R0 numpy.ndarray              `4 x 4`
             lidar2camera numpy.ndarray    `4 x 4`
             imu2lidar numpy.ndarray       `4 x 4`

    >>> P0, P1, P2, P3, R0, lidar2camera_m, imu2lidar_m = read_calib(calib_path)
    >>> extrinsic_m = np.matmul(R0, lidar2camera_m)
    >>> intrinsic_m = P2
    """
    with open(calib_path, 'r') as f:
        raw = f.readlines()
    # Pi 对应autoware标定文件的k，这里的k是畸变校正后的K，同理用的图像也是畸变校正后的图像
    # R0 不需要
    # Tr 外参矩阵，同样对应autoware外参
    P0 = np.array(list(map(float, raw[0].split()[1:]))).reshape((3, 4))
    P1 = np.array(list(map(float, raw[1].split()[1:]))).reshape((3, 4))
    P2 = np.array(list(map(float, raw[2].split()[1:]))).reshape((3, 4))
    P3 = np.array(list(map(float, raw[3].split()[1:]))).reshape((3, 4))
    R0 = np.array(list(map(float, raw[4].split()[1:]))).reshape((3, 3))
    R0 = np.hstack((R0, np.array([[0], [0], [0]])))
    R0 = np.vstack((R0, np.array([0, 0, 0, 1])))
    lidar2camera_m = np.array(list(map(float, raw[5].split()[1:]))).reshape((3, 4))
    lidar2camera_m = np.vstack((lidar2camera_m, np.array([0, 0, 0, 1])))
    imu2lidar_m = np.array(list(map(float, raw[6].split()[1:]))).reshape((3, 4))
    imu2lidar_m = np.vstack((imu2lidar_m, np.array([0, 0, 0, 1])))
    return P0, P1, P2, P3, R0, lidar2camera_m, imu2lidar_m

# pyqtgraph可视化渲染点云，这里不用看，专用ros+rviz
def vis_pointcloud(points, colors=None):
    """
    渲染显示雷达点云
    :param points:    numpy.ndarray  `N x 3`
    :param colors:    numpy.ndarray  `N x 3`  (0, 255)
    :return:
    """
    app = QApplication(sys.argv)
    if colors is not None:
        colors = colors / 255
        colors = np.hstack((colors, np.ones(shape=(colors.shape[0], 1))))
    else:
        colors = (1, 1, 1, 1)
    og_widget = gl.GLViewWidget()
    
    # 每个点的大小设为0.1
    point_size = np.zeros(points.shape[0], dtype=np.float16) + 0.1

    points_item1 = gl.GLScatterPlotItem(pos=points, size=point_size, color=colors, pxMode=False)
    og_widget.addItem(points_item1)

    # 作为对比
    points_item2 = gl.GLScatterPlotItem(pos=points, size=point_size, color=(1, 1, 1, 1), pxMode=False)

    # 原始点云z轴向上平移20，作为着色点云的对比
    points_item2.translate(0, 0, 20)
    og_widget.addItem(points_item2)

    og_widget.show()
    sys.exit(app.exec_())


def image2camera(point_in_image, intrinsic):
    """
    图像系到相机系反投影
    :param point_in_image: numpy.ndarray `N x 3` (u, v, z)
    :param intrinsic: numpy.ndarray `3 x 3` or `3 x 4`
    :return: numpy.ndarray `N x 3` (x, y, z)
    u = fx * X/Z + cx
    v = fy * Y/Z + cy
    X = (u - cx) * Z / fx
    Y = (v - cy) * z / fy
       [[fx, 0,  cx, -fxbi],
    K=  [0,  fy, cy],
        [0,  0,  1 ]]
    """
    if intrinsic.shape == (3, 3):  # 兼容kitti的P2, 对于没有平移的intrinsic添0
        intrinsic = np.hstack((intrinsic, np.zeros((3, 1))))

    u = point_in_image[:, 0]
    v = point_in_image[:, 1]
    z = point_in_image[:, 2]
    x = ((u - intrinsic[0, 2]) * z - intrinsic[0, 3]) / intrinsic[0, 0]
    y = ((v - intrinsic[1, 2]) * z - intrinsic[1, 3]) / intrinsic[1, 1]
    point_in_camera = np.vstack((x, y, z))
    return point_in_camera


def lidar2camera(point_in_lidar, extrinsic):
    """
    雷达系到相机系投影
    :param point_in_lidar: numpy.ndarray `N x 3`
    :param extrinsic: numpy.ndarray `4 x 4`
    :return: point_in_camera numpy.ndarray `N x 3`
    """
    # 点云加了1维度，变成N*4，再转置，变为4*N，可以与外参矩阵相乘
    point_in_lidar = np.hstack((point_in_lidar, np.ones(shape=(point_in_lidar.shape[0], 1)))).T
    # 乘法，(4,4)*(4,N)→(4,N),再丢掉最后一列，变为(3,N)
    point_in_camera = np.matmul(extrinsic, point_in_lidar)[:-1, :]  # (X, Y, Z)
    # 转置，(3,N)→(N,3)
    point_in_camera = point_in_camera.T
    return point_in_camera


def camera2image(point_in_camera, intrinsic):
    """
    相机系到图像系投影变换
    :param point_in_camera: point_in_camera numpy.ndarray `N x 3`   (x,y,z)
    :param intrinsic: numpy.ndarray `3 x 3` or `3 x 4`
    :return: point_in_image numpy.ndarray `N x 3` (u, v, z)
    """
    # N*3转置为3*N，方便后面矩阵乘法
    point_in_camera = point_in_camera.T
    # 后续相机系→图像系的投影中，会损失z轴(前向距离)信息，因此提前预留
    point_z = point_in_camera[-1] # 提取最后一列，即(x,y,z)的z

    # 同时兼容自己标定的3*3矩阵，和kitti标定文件的3*4矩阵
    if intrinsic.shape == (3, 3):  
        # 兼容kitti的P2, 对于没有平移的intrinsic添一列0: (3,3)+(3,1)-->(3,4)
        intrinsic = np.hstack((intrinsic, np.zeros((3, 1))))
    
    # 为了兼容, 点云矩阵添一列1: (3,N)+(1,N)-->(4,N)
    # 后面用自己的数据，为了简化提速，均可以去掉，全按照3走
    point_in_camera = np.vstack((point_in_camera, np.ones((1, point_in_camera.shape[1]))))

    # 相机系-->图像系投影，其中point_in_camera是雷达系-->相机系的投影结果
    # (3,4)*(4,N)-->(3,N)
    point_in_image = (np.matmul(intrinsic, point_in_camera) / point_z)  
    # print(point_in_image, point_in_image.shape)  # (3,N)

    # 图像系最后一列赋值，(3,N)中的3指(u,v,z),横轴为u，纵轴为v
    point_in_image[-1] = point_z 

    # (3,N)转置为(N,3)
    # 意义：每一个激光雷达点到像素点间的对应关系
    point_in_image = point_in_image.T

    return point_in_image


def lidar2image(point_in_lidar, extrinsic, intrinsic):
    """
    雷达系到图像系投影  获得(u, v, z)
    :param point_in_lidar: numpy.ndarray `N x 3`
    :param extrinsic: numpy.ndarray `4 x 4`
    :param intrinsic: numpy.ndarray `3 x 3` or `3 x 4`
    :return: point_in_image numpy.ndarray `N x 3` (u, v, z)
    """
    # 坐标系连续转换：激光雷达→相机→图像
    point_in_camera = lidar2camera(point_in_lidar, extrinsic)
    point_in_image = camera2image(point_in_camera, intrinsic)
    return point_in_image


def get_fov_mask(point_in_lidar, extrinsic, intrinsic, h, w):
    """
    获取fov内的点云mask, 即能够投影在图像上的点云mask
    :param point_in_lidar:   雷达点云 numpy.ndarray `N x 3`
    :param extrinsic:        外参 numpy.ndarray `4 x 4`
    :param intrinsic:        内参 numpy.ndarray `3 x 3` or `3 x 4`
    :param h:                图像高 int
    :param w:                图像宽 int
    :return: point_in_image: (u, v, z)  numpy.ndarray `N x 3`
    :return:                 numpy.ndarray  `1 x N`
    """
    # 雷达系-->相机系-->图像系的转换
    point_in_image = lidar2image(point_in_lidar, extrinsic, intrinsic)

    # 提取出最后一列(z)，保留z>0的所有点云
    front_bound = point_in_image[:, -1] > 0

    # u v 列的值四舍五入取整，因为像素坐标不可能有小数
    point_in_image[:, 0] = np.round(point_in_image[:, 0])
    point_in_image[:, 1] = np.round(point_in_image[:, 1])
    
    # 指保留u v值能落在图像上的，即0<u<w(idth) 0<v<h(eight)
    u_bound = np.logical_and(point_in_image[:, 0] >= 0, point_in_image[:, 0] < w)
    v_bound = np.logical_and(point_in_image[:, 1] >= 0, point_in_image[:, 1] < h)

    # 生成最终视场掩膜
    uv_bound = np.logical_and(u_bound, v_bound)
    mask = np.logical_and(front_bound, uv_bound)

    # 输出掩膜筛选后的点，以及视场掩膜
    return point_in_image[mask], mask


def get_point_in_image(point_in_lidar, extrinsic, intrinsic, h, w):
    """
    把雷达点云投影到图像上, 且经过筛选.  用这个就可以了.
    :param point_in_lidar:   雷达点云 numpy.ndarray `N x 3`
    :param extrinsic:        外参 numpy.ndarray `4 x 4`
    :param intrinsic:        内参 numpy.ndarray `3 x 3` or `3 x 4`
    :param h:                图像高 int
    :param w:                图像宽 int
    :return: point_in_image  (u, v, z)  numpy.ndarray `M x 3`  筛选掉了后面的点和不落在图像上的点
    :return: depth_image     numpy.ndarray `image_h x image_w` 深度图
    """
    point_in_image, mask = get_fov_mask(point_in_lidar, extrinsic, intrinsic, h, w)
    depth_image = np.zeros(shape=(h, w), dtype=np.float32)
    depth_image[point_in_image[:, 1].astype(np.int32), point_in_image[:, 0].astype(np.int32)] = point_in_image[:, 2]
    return point_in_image, depth_image


def depth_colorize(depth):
    """
    深度图着色渲染
    :param depth: numpy.ndarray `H x W`
    :return: numpy.ndarray `H x W x C'  RGB
    example:
    n = np.arange(90000).reshape((300, 300))
    colored = depth_colorize(n).astype(np.uint8)
    colored = cv2.cvtColor(colored, cv2.COLOR_RGB2BGR)
    cv2.imshow('test', colored)
    cv2.waitKey()
    """
    # 确定是否是2维
    assert depth.ndim == 2, 'depth image shape need to be `H x W`.'

    # 归一化为0-1
    depth = (depth - np.min(depth)) / (np.max(depth) - np.min(depth))

    # 彩图生成
    depth = 255 * cmap(depth)[:, :, :3]  # H, W, C
    return depth


def get_colored_depth(depth):
    """
    渲染深度图, depth_colorize函数的封装
    :param depth:  numpy.ndarray `H x W`
    :return:       numpy.ndarray `H x W x C'  RGB
    """
    if len(depth.shape) == 3:
        depth = depth.squeeze()

    # 深度图渲染为二维RGB可视化效果
    colored_depth = depth_colorize(depth).astype(np.uint8)
    colored_depth = cv2.cvtColor(colored_depth, cv2.COLOR_RGB2BGR)
    return colored_depth


def render_image_with_depth(color_image, depth_image, max_depth=None):
    """
    根据深度图渲染可见光图像, 在可见光图像上渲染点云
    :param color_image:  numpy.ndarray `H x W x C`
    :param depth_image:  numpy.ndarray `H x W`
    :param max_depth:  int 控制渲染效果
    :return:
    """
    # 不复制会报错
    depth_image = depth_image.copy()

    if max_depth is not None:
        depth_image = np.minimum(depth_image, max_depth)
    
    # 不复制会报错
    color_image = color_image.copy()

    colored_depth = get_colored_depth(depth_image)

    # 找出有效深度值(非0)的像素索引
    idx = depth_image != 0

    # 根据索引，将有效深度值替换到原始RGB图像
    color_image[idx] = colored_depth[idx]
    return color_image


if __name__ == '__main__':
    # 设置点云、图像路径
    image_path = '../data_example/3d_detection/image_2/000007.png'
    bin_path = '../data_example/3d_detection/velodyne/000007.bin'
    calib_path = '../data_example/3d_detection/calib/000007.txt'

    # 读取点云
    point_in_lidar = read_bin(bin_path)
    # 读取图像
    color_image = cv2.cvtColor(cv2.imread(image_path), cv2.COLOR_BGR2RGB)
    # 读取标定文件，包括相机内参矩阵、相机修正矩阵、相机-雷达外参矩阵
    _, _, P2, _, R0, lidar2camera_matrix, _ = read_calib(calib_path)
    # print(P2, R0, lidar2camera_matrix, sep='\n')
    intrinsic = P2                      # 内参
    extrinsic = np.matmul(R0, lidar2camera_matrix)  # 雷达到相机外参
    # 读取图像高、宽
    h, w = color_image.shape[:2]  # 图像高和宽

    # point_in_image是图像系(u,v,z)下的点云
    # mask是视场掩膜
    point_in_image, mask = get_fov_mask(point_in_lidar, extrinsic, intrinsic, h, w)

    # 获取在图像视野的雷达系(N,3)下有效点云
    valid_points = point_in_lidar[mask]

    # 获取给点云着色的颜色信息，colors (N,3),3指R G B
    colors = color_image[point_in_image[:, 1].astype(np.int32),
                         point_in_image[:, 0].astype(np.int32)]  # N x 3
    
    # 拼接生成着色点云(N,6),6指 x y z R G B
    colored_point = np.hstack((valid_points, colors))  # N x 6

    '''
    # 获取深度图
    '''
    # 生成全0图，用于定义变量
    sparse_depth_image = np.zeros(shape=(h, w), dtype='float32')

    # 先v后u，全部设为整数类型，将z轴的深度信息赋值过来，生成深度图
    sparse_depth_image[point_in_image[:, 1].astype(np.int32),
                       point_in_image[:, 0].astype(np.int32)] = point_in_image[:, 2]
    
    # 深度图渲染为二维RGB可视化效果
    colored_sparse_depth_image = get_colored_depth(sparse_depth_image)

    # 将深度图叠加到原始RGB图像，即点云投影到图像的最终可视化效果
    rendered_color_image = render_image_with_depth(color_image, sparse_depth_image)

    '''
    # 可视化
    '''
    # 点云投影到图像的渲染
    cv2.imshow('colored_sparse_depth', colored_sparse_depth_image)
    cv2.imshow('rendered_color_image', rendered_color_image.astype(np.uint8))
    # 图像投影到点云的渲染
    vis_pointcloud(points=valid_points, colors=colors)

    # 保存numpy数组, 用作ros点云发布数据
    # np.save('../data_example/points.npy', valid_points)   # (N, 3)  np.float32
    # np.save('../data_example/colors.npy', colors)         # (N, 3)  np.uint8  [0, 255]

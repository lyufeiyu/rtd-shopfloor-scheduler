"""保留原入口签名，统一使用产品的数据校验。"""
from product_data import load_product, periods, scheduler_args

parse_unavailable_periods = periods

def load_data(excel_path):
    return scheduler_args(load_product(excel_path))

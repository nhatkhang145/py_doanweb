from django import template
from decimal import Decimal

register = template.Library()

@register.filter(name='vnd')
def vnd_format(value):
    """
    Format số tiền theo kiểu Việt Nam: 1000000 -> 1.000.000đ
    """
    try:
        # Chuyển về số nguyên
        if isinstance(value, Decimal):
            value = int(value)
        elif isinstance(value, float):
            value = int(value)
        elif isinstance(value, str):
            value = int(float(value))
        
        # Format với dấu chấm phân cách hàng nghìn
        formatted = "{:,.0f}".format(value).replace(",", ".")
        return f"{formatted}đ"
    except (ValueError, TypeError):
        return value

@register.filter(name='vnd_no_symbol')
def vnd_no_symbol(value):
    """
    Format số tiền không có ký hiệu đ: 1000000 -> 1.000.000
    """
    try:
        if isinstance(value, Decimal):
            value = int(value)
        elif isinstance(value, float):
            value = int(value)
        elif isinstance(value, str):
            value = int(float(value))
        
        formatted = "{:,.0f}".format(value).replace(",", ".")
        return formatted
    except (ValueError, TypeError):
        return value


@register.filter(name='format_price')
def format_price(value):
    """
    Format số tiền theo kiểu Việt Nam: 1000000 -> 1.000.000đ
    Alias của filter vnd
    """
    try:
        if isinstance(value, Decimal):
            value = int(value)
        elif isinstance(value, float):
            value = int(value)
        elif isinstance(value, str):
            value = int(float(value))
        
        formatted = "{:,.0f}".format(value).replace(",", ".")
        return f"{formatted}đ"
    except (ValueError, TypeError):
        return "0đ"

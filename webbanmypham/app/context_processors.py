from .cart import Cart
from .models import Wishlist

def cart_context(request):

    return {'cart': Cart(request)}

def wishlist_context(request):
    if request.user.is_authenticated:
        wishlist_count = Wishlist.objects.filter(user=request.user).count()
        wishlist_product_ids = list(Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True))
    else:
        wishlist_count = 0
        wishlist_product_ids = []
    return {
        'wishlist_count': wishlist_count,
        'wishlist_product_ids': wishlist_product_ids,
    }
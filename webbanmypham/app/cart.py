from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from .models import Product, WeekendDeal

class Cart:
    def __init__(self, request):
       
        self.session = request.session
        cart = self.session.get(settings.CART_SESSION_ID)
        if not cart:
         
            cart = self.session[settings.CART_SESSION_ID] = {}
        self.cart = cart

   
        if 'shipping_method' not in self.session:
            self.session['shipping_method'] = 'fast'

    def get_deal_price(self, product):
      
        now = timezone.now()
        deal = WeekendDeal.objects.filter(
            product=product,
            is_active=True,
            start_time__lte=now,
            end_time__gte=now
        ).first()
        
        if deal:
            return deal.deal_price
        return None

    def add(self, product, quantity=1, override_quantity=False):
     
        product_id = str(product.id)
        
      
        deal_price = self.get_deal_price(product)
        if deal_price:
            price = deal_price
        elif product.sale_price > 0:
            price = product.sale_price
        else:
            price = product.price
        
       
        if product_id not in self.cart:
            self.cart[product_id] = {
                'quantity': 0,
                'price': str(price)  
            }
        else:
            self.cart[product_id]['price'] = str(price)
        
      
        if override_quantity:
            self.cart[product_id]['quantity'] = quantity
        else:
            self.cart[product_id]['quantity'] += quantity
            
        self.save()

    def save(self):
       
        self.session.modified = True

    def remove(self, product):
      
        product_id = str(product.id)
        if product_id in self.cart:
            del self.cart[product_id]
            self.save()

    def decrease(self, product):
       
        product_id = str(product.id)
        if product_id in self.cart:
            self.cart[product_id]['quantity'] -= 1
            if self.cart[product_id]['quantity'] <= 0:
                del self.cart[product_id]
            self.save()

    def __iter__(self):
       
        product_ids = self.cart.keys()
       
        products = Product.objects.filter(id__in=product_ids)
        
        cart = self.cart.copy()
        
        for product in products:
            cart[str(product.id)]['product'] = product
            
        for item in cart.values():
           
            _price = item.get('price')
            if _price is None:
                _price = 0
            
            item['price'] = Decimal(str(_price))
            item['total_price'] = item['price'] * item['quantity']
            yield item

    def __len__(self):
        
        return sum(item['quantity'] for item in self.cart.values())

    def get_total_price(self):
       
        return sum(Decimal(item['price']) * item['quantity'] for item in self.cart.values())

    def clear(self):
       
        del self.session[settings.CART_SESSION_ID]
        self.save()

    
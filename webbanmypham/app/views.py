from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.db.models import Avg, Q, Sum
from django.utils import timezone

from datetime import datetime, timedelta
from decimal import Decimal
import random
import logging

from .models import (
    CustomerProfile, Product, Category, Order, Brand, 
    OrderItem, UserAddress, Review, WeekendDeal, Wishlist,
    SpamKeyword, ProductBatch
)
from .forms import RegisterForm, LoginForm, ProductForm
from .cart import Cart
from .ai_utils import analyze_sentiment
from .vnpay import VNPay, get_client_ip
from .services.review_service import is_review_spam

logger = logging.getLogger(__name__)


def is_admin(user):
    try:
        return user.is_superuser or user.profile.role in [1, 2]
    except:
        return False


def get_active_deals():
    now = timezone.now()
    return WeekendDeal.objects.filter(
        is_active=True,
        start_time__lte=now,
        end_time__gte=now
    ).select_related('product')


def attach_deals_to_products(products):
    deal_prices = {deal.product_id: deal for deal in get_active_deals()}
    for product in products:
        product.active_deal = deal_prices.get(product.id)
    return products


def home(request):
    offer_products = list(Product.objects.all().order_by('-id')[:8])
    
    trending_products = list(Product.objects.filter(sale_price__gt=0)[:8])
    if not trending_products:
        trending_products = list(Product.objects.all()[:8])
    
    now = timezone.now()
    weekend_deals = WeekendDeal.objects.filter(
        is_active=True,
        start_time__lte=now,
        end_time__gte=now
    ).select_related('product').order_by('-priority', '-created_at')[:5]
    
    offer_products = attach_deals_to_products(offer_products)
    trending_products = attach_deals_to_products(trending_products)

    context = {
        'offer_products': offer_products,
        'trending_products': trending_products,
        'weekend_deals': weekend_deals,
    }
    return render(request, 'app/home.html', context)


def shop(request):
    product_list = Product.objects.all().order_by('-id')
    categories = Category.objects.all()
    
    active_category = request.GET.get('category')
    if active_category:
        product_list = product_list.filter(category_id=active_category)
    
    active_brand = request.GET.get('brand')
    if active_brand:
        product_list = product_list.filter(brand_id=active_brand)
    
    sidebar_data = []
    for category in categories:
        brands = Brand.objects.filter(product__category=category).distinct()
        sidebar_data.append({
            'category': category,
            'brands': brands
        })
    
    paginator = Paginator(product_list, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    page_obj = attach_deals_to_products(page_obj)

    context = {
        'page_obj': page_obj,
        'categories': categories,
        'sidebar_data': sidebar_data,
        'active_category': active_category,
        'active_brand': active_brand
    }
    return render(request, 'app/shop.html', context)


def product_detail(request, id):
    product = get_object_or_404(Product, id=id)
    
    related_products = Product.objects.filter(category=product.category).exclude(id=id)[:4]
    related_products = attach_deals_to_products(related_products)
    
    reviews = Review.objects.filter(product=product, is_approved=True).order_by('-created_at')
    
    now = timezone.now()
    active_deal = WeekendDeal.objects.filter(
        product=product,
        is_active=True,
        start_time__lte=now,
        end_time__gte=now
    ).first()
    
    context = {
        'product': product,
        'related_products': related_products,
        'reviews': reviews,
        'active_deal': active_deal,
    }
    return render(request, 'app/product_detail.html', context)


def search(request):
    searched = request.GET.get('searched', '')
    products = Product.objects.filter(name__icontains=searched) if searched else []
    
    return render(request, 'app/search.html', {
        'searched': searched,
        'products': products
    })


def register(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()

            CustomerProfile.objects.create(
                user=user,
                fullname=form.cleaned_data['fullname'],
            )
            
            messages.success(request, "Đăng ký thành công! Hãy đăng nhập.")
            return redirect('login')
        else:
            messages.error(request, "Đăng ký thất bại. Vui lòng kiểm tra lại thông tin.")
    else:
        form = RegisterForm()
    
    return render(request, 'app/register.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(request, username=username, password=password)
            
            if user is not None:
                login(request, user)
                
                next_url = request.GET.get('next')
                if next_url:
                    return redirect(next_url)

                if user.is_superuser:
                    return redirect('admin_dashboard')
                
                try:
                    user_role = user.profile.role
                    if user_role in [1, 2]:
                        return redirect('admin_dashboard')
                    return redirect('home')
                except:
                    return redirect('home')
            else:
                messages.error(request, "Sai tên đăng nhập hoặc mật khẩu!")
    else:
        form = LoginForm()
        
    return render(request, 'app/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('home')


def add_to_cart(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    quantity = int(request.GET.get('quantity', 1))
    
    for _ in range(quantity):
        cart.add(product=product)
    
    return redirect('cart_detail')


def add_to_cart_ajax(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    quantity = int(request.GET.get('quantity', 1))
    
    for _ in range(quantity):
        cart.add(product=product)
    
    return JsonResponse({
        'success': True,
        'message': f'Đã thêm "{product.name}" vào giỏ hàng!',
        'cart_count': len(cart),
        'cart_total': str(cart.get_total_price())
    })


def buy_now(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    quantity = int(request.GET.get('quantity', 1))
    
    now = timezone.now()
    deal = WeekendDeal.objects.filter(
        product=product,
        is_active=True,
        start_time__lte=now,
        end_time__gte=now
    ).first()
    
    if deal:
        price = deal.deal_price
    elif product.sale_price > 0:
        price = product.sale_price
    else:
        price = product.price
    
    request.session['buy_now_item'] = {
        'product_id': product.id,
        'product_name': product.name,
        'product_image': product.image.url if product.image else '',
        'quantity': quantity,
        'price': str(price),
        'total': str(price * quantity)
    }
    
    return redirect('checkout')


def cart_detail(request):
    cart = Cart(request)
    return render(request, 'app/cart.html', {'cart': cart})


def update_cart(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    cart.decrease(product)
    return redirect('cart_detail')


def remove_from_cart(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    cart.remove(product)
    return redirect('cart_detail')


@login_required(login_url='login')
def wishlist(request):
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__category')
    
    deal_prices = {deal.product_id: deal for deal in get_active_deals()}
    for item in wishlist_items:
        item.product.active_deal = deal_prices.get(item.product.id)
    
    return render(request, 'app/wishlist.html', {'wishlist_items': wishlist_items})


def toggle_wishlist_ajax(request, product_id):
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': False,
            'message': 'Vui lòng đăng nhập để sử dụng tính năng này!',
            'redirect': '/login/'
        })
    
    product = get_object_or_404(Product, id=product_id)
    wishlist_item = Wishlist.objects.filter(user=request.user, product=product).first()
    
    if wishlist_item:
        wishlist_item.delete()
        return JsonResponse({
            'success': True,
            'action': 'removed',
            'message': f'Đã xóa "{product.name}" khỏi danh sách yêu thích!',
            'wishlist_count': Wishlist.objects.filter(user=request.user).count()
        })
    else:
        Wishlist.objects.create(user=request.user, product=product)
        return JsonResponse({
            'success': True,
            'action': 'added',
            'message': f'Đã thêm "{product.name}" vào danh sách yêu thích!',
            'wishlist_count': Wishlist.objects.filter(user=request.user).count()
        })


@login_required(login_url='login')
def remove_from_wishlist(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    Wishlist.objects.filter(user=request.user, product=product).delete()
    messages.success(request, f'Đã xóa "{product.name}" khỏi danh sách yêu thích!')
    return redirect('wishlist')


@login_required(login_url='login')
def checkout(request):
    cart = Cart(request)
    buy_now_item = request.session.get('buy_now_item')
    is_buy_now = buy_now_item is not None
    
    if not is_buy_now and len(cart) == 0:
        messages.warning(request, "Giỏ hàng của bạn đang trống!")
        return redirect('shop')

    if request.method == 'POST':
        selected_address_id = request.POST.get('selected_address')
        
        if selected_address_id == 'new':
            fullname = request.POST.get('fullname')
            phone = request.POST.get('phone')
            address_text = f"{request.POST.get('address')}, {request.POST.get('city')}"
        else:
            try:
                addr = UserAddress.objects.get(id=selected_address_id, user=request.user)
                fullname = addr.receiver_name
                phone = addr.phone
                address_text = f"{addr.detail_address}, {addr.district}, {addr.city}"
            except UserAddress.DoesNotExist:
                messages.error(request, "Địa chỉ không hợp lệ!")
                return redirect('checkout')

        order_code = f"ORD-{random.randint(100000, 999999)}"
        
        if is_buy_now:
            total_price = Decimal(buy_now_item['total'])
        else:
            total_price = cart.get_total_price()

        new_order = Order.objects.create(
            order_code=order_code,
            user=request.user,
            fullname=fullname,
            phone=phone,
            address=address_text,
            total_money=total_price,
            shipping_fee=0,
            final_money=total_price,
            payment_method=request.POST.get('payment_method'),
            note=request.POST.get('note')
        )
        
        if is_buy_now:
            product = get_object_or_404(Product, id=buy_now_item['product_id'])
            OrderItem.objects.create(
                order=new_order,
                product=product,
                product_name=buy_now_item['product_name'],
                price=Decimal(buy_now_item['price']),
                quantity=buy_now_item['quantity']
            )
            del request.session['buy_now_item']
        else:
            for item in cart:
                OrderItem.objects.create(
                    order=new_order,
                    product=item['product'],
                    product_name=item['product'].name,
                    price=item['price'],
                    quantity=item['quantity']
                )
            cart.clear()
        
        if request.POST.get('payment_method') == 'VNPAY':
            request.session['pending_order_id'] = new_order.id
            return redirect('vnpay_payment', order_id=new_order.id)
        
        messages.success(request, f"Đặt hàng thành công! Mã đơn: {order_code}")
        return redirect('home')

    try:
        user_addresses = request.user.addresses.all()
    except:
        user_addresses = []
    
    context = {
        'user_addresses': user_addresses,
        'is_buy_now': is_buy_now,
    }
    
    if is_buy_now:
        product = get_object_or_404(Product, id=buy_now_item['product_id'])
        context['buy_now_item'] = buy_now_item
        context['buy_now_product'] = product
    else:
        context['cart'] = cart
        
    return render(request, 'app/checkout.html', context)


@login_required(login_url='login')
def vnpay_payment(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    
    if order.payment_status:
        messages.warning(request, "Đơn hàng này đã được thanh toán!")
        return redirect('home')
    
    ip_address = get_client_ip(request)
    return_url = request.build_absolute_uri('/payment/vnpay/return/')
    
    vnpay = VNPay()
    payment_url = vnpay.build_payment_url(
        return_url=return_url,
        order_code=order.order_code,
        amount=int(order.final_money),
        order_desc=f"Thanh toan don hang {order.order_code}",
        ip_address=ip_address
    )
    
    return redirect(payment_url)


def vnpay_return(request):
    response_data = request.GET.dict()
    
    if not response_data:
        messages.error(request, "Không nhận được phản hồi từ VNPay!")
        return redirect('home')
    
    vnpay = VNPay()
    is_valid = vnpay.validate_response(response_data)
    
    if is_valid:
        order_code = response_data.get('vnp_TxnRef', '')
        response_code = response_data.get('vnp_ResponseCode', '')
        transaction_id = response_data.get('vnp_TransactionNo', '')
        bank_code = response_data.get('vnp_BankCode', '')
        
        try:
            order = Order.objects.get(order_code=order_code)
            
            if response_code == '00':
                order.payment_status = True
                order.order_status = 'confirmed'
                order.save()
                
                messages.success(
                    request,
                    f"✓ Thanh toán thành công!\n"
                    f"Mã đơn hàng: {order_code}\n"
                    f"Mã giao dịch: {transaction_id}\n"
                    f"Ngân hàng: {bank_code}"
                )
            else:
                error_msg = VNPay.get_response_message(response_code)
                messages.error(request, f"✗ Thanh toán thất bại: {error_msg}")
                
        except Order.DoesNotExist:
            messages.error(request, "Không tìm thấy đơn hàng!")
    else:
        messages.error(request, "✗ Chữ ký không hợp lệ! Giao dịch bị nghi ngờ giả mạo.")
    
    return redirect('home')


@login_required(login_url='login')
def my_orders(request):
    filter_status = request.GET.get('status', 'all')
    orders = Order.objects.filter(user=request.user).order_by('-created_at')
    
    pending_count = orders.filter(order_status='pending').count()
    confirmed_count = orders.filter(order_status='confirmed').count()
    shipping_count = orders.filter(order_status='shipping').count()
    completed_count = orders.filter(order_status='completed').count()
    cancelled_count = orders.filter(order_status='cancelled').count()
    
    if filter_status != 'all':
        orders = orders.filter(order_status=filter_status)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        order_id = request.POST.get('order_id')
        
        if action == 'cancel':
            try:
                order = Order.objects.get(id=order_id, user=request.user)
                if order.order_status in ['pending', 'confirmed']:
                    order.order_status = 'cancelled'
                    order.save()
                    messages.success(request, f"✓ Đã hủy đơn hàng #{order.order_code}")
                else:
                    messages.error(request, "✗ Không thể hủy đơn hàng ở trạng thái này!")
            except Order.DoesNotExist:
                messages.error(request, "✗ Đơn hàng không tồn tại!")
        
        return redirect('my_orders')
    
    context = {
        'orders': orders,
        'filter_status': filter_status,
        'pending_count': pending_count,
        'confirmed_count': confirmed_count,
        'shipping_count': shipping_count,
        'completed_count': completed_count,
        'cancelled_count': cancelled_count,
    }
    
    return render(request, 'app/orders.html', context)


@login_required(login_url='login')
def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    order_items = order.items.all()
    
    return render(request, 'app/order_detail.html', {
        'order': order,
        'order_items': order_items,
    })


@login_required(login_url='login')
def profile(request):
    profile, created = CustomerProfile.objects.get_or_create(user=request.user)
    
    if request.method == 'POST':
        profile.fullname = request.POST.get('fullname')
        profile.phone = request.POST.get('phone')
        
        birth_day = request.POST.get('birthDay')
        birth_month = request.POST.get('birthMonth')
        birth_year = request.POST.get('birthYear')
        
        if birth_day and birth_month and birth_year:
            try:
                profile.skin_concerns = {
                    'birth_day': birth_day,
                    'birth_month': birth_month,
                    'birth_year': birth_year
                }
            except:
                pass
        
        if request.FILES.get('avatarFile'):
            profile.avatar = request.FILES['avatarFile']
        
        profile.save()
        messages.success(request, '✓ Cập nhật hồ sơ thành công!')
        return redirect('profile')
    
    birth_data = profile.skin_concerns if isinstance(profile.skin_concerns, dict) else {}
    
    context = {
        'profile': profile,
        'birth_day': birth_data.get('birth_day', ''),
        'birth_month': birth_data.get('birth_month', ''),
        'birth_year': birth_data.get('birth_year', ''),
    }
    
    return render(request, 'app/profile.html', context)


@login_required(login_url='login')
def submit_review(request, product_id):
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id)
        text_comment = request.POST.get('comment', '').strip()
        rating = request.POST.get('rating')

        logger.info(f"========== SUBMIT REVIEW ==========")
        logger.info(f"User: {request.user.username}, Product: {product.id}")
        logger.info(f"Comment length: {len(text_comment)}, Rating: {rating}")

        if not text_comment:
            logger.warning(f"Empty comment from user {request.user.username}")
            return redirect('product_detail', id=product_id)

        try:
            ai_label, ai_score = analyze_sentiment(text_comment)
            logger.info(f"✓ AI Result - Label: {ai_label}, Score: {ai_score}")
        except Exception as e:
            logger.error(f"✗ AI Error: {e}", exc_info=True)
            ai_label, ai_score = 'NEU', 50.0
        
        spam_result = is_review_spam(text_comment, int(rating) if rating else 5)
        logger.info(f"Spam check: {spam_result}")

        review = Review.objects.create(
            user=request.user,
            product=product,
            comment=text_comment,
            rating=rating,
            sentiment=ai_label,
            confidence_score=ai_score,
            is_approved=True,
            is_spam=spam_result['is_spam'],
            spam_reason=spam_result['reason'] if spam_result['is_spam'] else ''
        )
        
        if spam_result['is_spam']:
            review.sentiment = 'SPAM'
            review.save()
            logger.info(f"✓ Updated to SPAM - ID: {review.id}")
        
        logger.info(f"========== END SUBMIT REVIEW ==========")
        
    return redirect('product_detail', id=product_id)


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_dashboard(request):
    customer_count = CustomerProfile.objects.count()
    product_count = Product.objects.count()
    order_count = Order.objects.count()
    
    low_stock_count = Product.objects.filter(stock_quantity__lte=5, status=True).count()
    dead_stock_count = Product.objects.filter(stock_quantity__gt=20, status=True).count()

    context = {
        'customer_count': customer_count,
        'product_count': product_count,
        'order_count': order_count,
        'low_stock_count': low_stock_count,
        'dead_stock_count': dead_stock_count,
    }
    return render(request, 'app/my_admin/dashboard.html', context)


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_customers(request):
    customers = CustomerProfile.objects.all()
    return render(request, 'app/my_admin/customers.html', {'customers': customers})


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_customer_detail(request, id):
    customer = get_object_or_404(CustomerProfile, id=id)
    recent_orders = Order.objects.filter(user=customer.user).order_by('-created_at')[:10]
    
    return render(request, 'app/my_admin/customer_detail.html', {
        'customer': customer,
        'recent_orders': recent_orders
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_products(request):
    products_list = Product.objects.all().order_by('-id')
    categories = Category.objects.all()
    brands = Brand.objects.all()
    
    paginator = Paginator(products_list, 10)
    page_number = request.GET.get('page')
    products = paginator.get_page(page_number)
    
    return render(request, 'app/my_admin/products.html', {
        'products': products,
        'categories': categories,
        'brands': brands,
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_product_add(request):
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('admin_products')
    else:
        form = ProductForm()
    
    return render(request, 'app/my_admin/product_form.html', {
        'form': form,
        'title': 'Thêm sản phẩm'
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_product_edit(request, id):
    product = get_object_or_404(Product, id=id)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            return redirect('admin_products')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'app/my_admin/product_form.html', {
        'form': form,
        'title': 'Cập nhật sản phẩm'
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_categories(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        slug = request.POST.get('slug') or None
        parent_id = request.POST.get('parent') or None
        description = request.POST.get('description', '')
        image = request.FILES.get('image')
        
        try:
            category = Category.objects.create(
                name=name,
                slug=slug,
                parent_id=parent_id if parent_id else None,
                image=image
            )
            messages.success(request, f'✓ Đã thêm danh mục "{category.name}"')
        except Exception as e:
            messages.error(request, f'✗ Lỗi: {str(e)}')
        
        return redirect('admin_categories')
    
    categories = Category.objects.all().order_by('parent__id', 'name')
    parents = Category.objects.filter(parent__isnull=True)
    
    for cat in categories:
        cat.product_count = cat.product_set.count()
    
    return render(request, 'app/my_admin/categories.html', {
        'categories': categories,
        'parents': parents,
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_category_edit(request, id):
    category = get_object_or_404(Category, id=id)
    
    if request.method == 'POST':
        category.name = request.POST.get('name')
        category.slug = request.POST.get('slug') or None
        parent_id = request.POST.get('parent') or None
        category.parent_id = parent_id if parent_id else None
        
        if 'image' in request.FILES:
            category.image = request.FILES['image']
        
        try:
            category.save()
            messages.success(request, f'✓ Đã cập nhật danh mục "{category.name}"')
            return redirect('admin_categories')
        except Exception as e:
            messages.error(request, f'✗ Lỗi: {str(e)}')
    
    parents = Category.objects.filter(parent__isnull=True).exclude(id=category.id)
    
    return render(request, 'app/my_admin/categories_edit.html', {
        'category': category,
        'parents': parents,
        'form': category,
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_category_delete(request, id):
    category = get_object_or_404(Category, id=id)
    name = category.name
    
    try:
        category.delete()
        messages.success(request, f'✓ Đã xóa danh mục "{name}"')
    except Exception as e:
        messages.error(request, f'✗ Không thể xóa: {str(e)}')
    
    return redirect('admin_categories')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_brands(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        origin = request.POST.get('origin', '')
        logo = request.FILES.get('logo')
        
        try:
            brand = Brand.objects.create(
                name=name,
                origin=origin,
                logo=logo
            )
            messages.success(request, f'✓ Đã thêm thương hiệu "{brand.name}"')
        except Exception as e:
            messages.error(request, f'✗ Lỗi: {str(e)}')
        
        return redirect('admin_brands')
    
    brands = Brand.objects.all().order_by('name')
    categories = Category.objects.filter(parent__isnull=True)
    
    return render(request, 'app/my_admin/brands.html', {
        'brands': brands,
        'categories': categories,
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_brand_delete(request, id):
    brand = get_object_or_404(Brand, id=id)
    name = brand.name
    
    try:
        brand.delete()
        messages.success(request, f'✓ Đã xóa thương hiệu "{name}"')
    except Exception as e:
        messages.error(request, f'✗ Không thể xóa: {str(e)}')
    
    return redirect('admin_brands')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_orders(request):
    status_filter = request.GET.get('status', '')
    search_query = request.GET.get('search', '')

    orders_list = Order.objects.all().order_by('-created_at')

    if status_filter and status_filter != 'all':
        orders_list = orders_list.filter(order_status=status_filter)

    if search_query:
        orders_list = orders_list.filter(
            Q(order_code__icontains=search_query) |
            Q(fullname__icontains=search_query)
        )

    paginator = Paginator(orders_list, 10)
    page_number = request.GET.get('page')
    orders = paginator.get_page(page_number)

    return render(request, 'app/my_admin/orders.html', {
        'orders': orders,
        'current_status': status_filter,
        'search_query': search_query,
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_order_detail(request, id):
    order = get_object_or_404(Order, id=id)
    order_items = OrderItem.objects.filter(order=order)
    status_choices = Order.STATUS_CHOICES

    return render(request, 'app/my_admin/order-detail.html', {
        'order': order,
        'order_items': order_items,
        'status_choices': status_choices
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def update_order_status(request, id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=id)
        new_status = request.POST.get('status')
        
        if new_status:
            order.order_status = new_status
            if new_status == 'completed':
                order.payment_status = True
            order.save()
            messages.success(request, f"Cập nhật trạng thái đơn {order.order_code} thành công!")
        else:
            messages.error(request, "Vui lòng chọn trạng thái hợp lệ.")

    return redirect('admin_order_detail', id=id)


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_reviews(request):
    reviews = Review.objects.all().order_by('-created_at')
    
    for review in reviews:
        is_spam_flag = getattr(review, 'is_spam', False)
        if not is_spam_flag and review.comment:
            spam_result = is_review_spam(review.comment, review.rating)
            if spam_result['is_spam']:
                review.is_spam = True
                review.spam_reason = spam_result['reason']
                review.sentiment = 'SPAM'
                review.save()
    
    valid_reviews = reviews.exclude(is_spam=True)
    total_reviews = valid_reviews.count()
    spam_count = reviews.filter(is_spam=True).count()
    
    pos_percent = neg_percent = 0
    avg_rating = 0.0
    
    if total_reviews > 0:
        pos_count = sum(1 for r in valid_reviews if getattr(r, 'sentiment', 'NEU') == 'POS')
        neg_count = sum(1 for r in valid_reviews if getattr(r, 'sentiment', 'NEU') == 'NEG')
        
        pos_percent = round((pos_count / total_reviews) * 100)
        neg_percent = round((neg_count / total_reviews) * 100)
        avg_rating = round(valid_reviews.aggregate(Avg('rating'))['rating__avg'] or 0, 1)
    
    return render(request, 'app/my_admin/reviews.html', {
        'reviews': reviews,
        'total_reviews': total_reviews,
        'spam_count': spam_count,
        'pos_percent': pos_percent,
        'neg_percent': neg_percent,
        'avg_rating': avg_rating
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_inventory_alerts(request):
    LOW_STOCK_THRESHOLD = 5
    DEAD_STOCK_DAYS = 30
    HIGH_STOCK_THRESHOLD = 20

    alerts = []
    products = Product.objects.filter(status=True)
    time_threshold = timezone.now() - timedelta(days=DEAD_STOCK_DAYS)

    for p in products:
        if p.stock_quantity <= LOW_STOCK_THRESHOLD:
            alerts.append({
                'product': p,
                'type': 'LOW_STOCK',
                'level': 'critical',
                'message': 'Sắp hết hàng',
                'suggestion': 'Nhập thêm hàng ngay',
                'icon': 'bx-import',
                'css_class': 'restock'
            })
            continue

        recent_sales = OrderItem.objects.filter(
            product=p,
            order__created_at__gte=time_threshold
        ).aggregate(total_sold=Sum('quantity'))['total_sold'] or 0

        if p.stock_quantity > HIGH_STOCK_THRESHOLD and recent_sales == 0:
            alerts.append({
                'product': p,
                'type': 'DEAD_STOCK',
                'level': 'warning',
                'message': f'Tồn {p.stock_quantity} nhưng không bán được trong {DEAD_STOCK_DAYS} ngày',
                'suggestion': 'Giảm giá / Flash Sale',
                'icon': 'bxs-offer',
                'css_class': 'discount'
            })

    total_low = sum(1 for a in alerts if a['type'] == 'LOW_STOCK')
    total_dead = sum(1 for a in alerts if a['type'] == 'DEAD_STOCK')

    return render(request, 'app/my_admin/inventory_alerts.html', {
        'alerts': alerts,
        'total_low': total_low,
        'total_dead': total_dead,
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_spam_keywords(request):
    category_filter = request.GET.get('category', '')
    
    if category_filter:
        keywords = SpamKeyword.objects.filter(category=category_filter).order_by('-severity', 'keyword')
    else:
        keywords = SpamKeyword.objects.all().order_by('-severity', 'keyword')
    
    total_keywords = SpamKeyword.objects.count()
    active_keywords = SpamKeyword.objects.filter(is_active=True).count()
    inactive_keywords = total_keywords - active_keywords
    
    return render(request, 'app/my_admin/spam_keywords.html', {
        'keywords': keywords,
        'total_keywords': total_keywords,
        'active_keywords': active_keywords,
        'inactive_keywords': inactive_keywords,
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_spam_keywords_create(request):
    if request.method == 'POST':
        keyword = request.POST.get('keyword', '').strip()
        category = request.POST.get('category', 'OTHER')
        severity = int(request.POST.get('severity', 100))
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        
        if keyword:
            try:
                SpamKeyword.objects.create(
                    keyword=keyword,
                    category=category,
                    severity=severity,
                    description=description,
                    is_active=is_active
                )
                messages.success(request, f'✓ Đã thêm keyword "{keyword}"')
                
                from django.core.cache import cache
                cache.delete('spam_keywords_active')
            except Exception as e:
                messages.error(request, f'✗ Lỗi: {str(e)}')
        else:
            messages.error(request, '✗ Vui lòng nhập từ khóa')
    
    return redirect('admin_spam_keywords')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_spam_keywords_edit(request, keyword_id):
    keyword = get_object_or_404(SpamKeyword, id=keyword_id)
    
    if request.method == 'POST':
        keyword.keyword = request.POST.get('keyword', keyword.keyword).strip()
        keyword.category = request.POST.get('category', keyword.category)
        keyword.severity = int(request.POST.get('severity', keyword.severity))
        keyword.description = request.POST.get('description', '').strip()
        keyword.is_active = request.POST.get('is_active') == 'on'
        
        try:
            keyword.save()
            messages.success(request, f'✓ Đã cập nhật keyword "{keyword.keyword}"')
            
            from django.core.cache import cache
            cache.delete('spam_keywords_active')
        except Exception as e:
            messages.error(request, f'✗ Lỗi: {str(e)}')
        
        return redirect('admin_spam_keywords')
    
    return render(request, 'app/my_admin/spam_keyword_edit.html', {'keyword': keyword})


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_spam_keywords_toggle(request, keyword_id):
    if request.method == 'POST':
        keyword = get_object_or_404(SpamKeyword, id=keyword_id)
        keyword.is_active = not keyword.is_active
        keyword.save()
        
        status = "bật" if keyword.is_active else "tắt"
        messages.success(request, f'✓ Đã {status} keyword "{keyword.keyword}"')
        
        from django.core.cache import cache
        cache.delete('spam_keywords_active')
    
    return redirect('admin_spam_keywords')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_spam_keywords_delete(request, keyword_id):
    if request.method == 'POST':
        keyword = get_object_or_404(SpamKeyword, id=keyword_id)
        name = keyword.keyword
        
        try:
            keyword.delete()
            messages.success(request, f'✓ Đã xóa keyword "{name}"')
            
            from django.core.cache import cache
            cache.delete('spam_keywords_active')
        except Exception as e:
            messages.error(request, f'✗ Không thể xóa: {str(e)}')
    
    return redirect('admin_spam_keywords')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_deals(request):
    deals = WeekendDeal.objects.select_related('product').all().order_by('-priority', '-created_at')
    products = Product.objects.filter(status=True).order_by('name')
    
    return render(request, 'app/my_admin/deals.html', {
        'deals': deals,
        'products': products,
        'now': timezone.now(),
    })


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_deal_create(request):
    if request.method == 'POST':
        try:
            product_id = request.POST.get('product')
            product = get_object_or_404(Product, id=product_id)
            
            deal = WeekendDeal(
                product=product,
                title=request.POST.get('title', 'Hot Deal Cuối Tuần'),
                subtitle=request.POST.get('subtitle', ''),
                description=request.POST.get('description', ''),
                deal_price=request.POST.get('deal_price'),
                start_time=request.POST.get('start_time'),
                end_time=request.POST.get('end_time'),
                max_quantity=request.POST.get('max_quantity', 0) or 0,
                priority=request.POST.get('priority', 0) or 0,
                is_active=request.POST.get('is_active') == 'on',
            )
            
            if 'deal_image' in request.FILES:
                deal.deal_image = request.FILES['deal_image']
            
            deal.save()
            messages.success(request, f'✓ Tạo deal "{deal.title}" thành công!')
        except Exception as e:
            messages.error(request, f'✗ Lỗi: {str(e)}')
    
    return redirect('admin_deals')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_deal_edit(request, deal_id):
    deal = get_object_or_404(WeekendDeal, id=deal_id)
    
    if request.method == 'POST':
        try:
            product_id = request.POST.get('product')
            deal.product = get_object_or_404(Product, id=product_id)
            deal.title = request.POST.get('title', deal.title)
            deal.subtitle = request.POST.get('subtitle', '')
            deal.description = request.POST.get('description', '')
            deal.deal_price = request.POST.get('deal_price')
            deal.start_time = request.POST.get('start_time')
            deal.end_time = request.POST.get('end_time')
            deal.max_quantity = request.POST.get('max_quantity', 0) or 0
            deal.priority = request.POST.get('priority', 0) or 0
            deal.is_active = request.POST.get('is_active') == 'on'
            
            if 'deal_image' in request.FILES:
                deal.deal_image = request.FILES['deal_image']
            
            deal.save()
            messages.success(request, f'✓ Cập nhật deal "{deal.title}" thành công!')
        except Exception as e:
            messages.error(request, f'✗ Lỗi: {str(e)}')
    
    return redirect('admin_deals')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_deal_toggle(request, deal_id):
    deal = get_object_or_404(WeekendDeal, id=deal_id)
    deal.is_active = not deal.is_active
    deal.save()
    
    status = "kích hoạt" if deal.is_active else "tạm dừng"
    messages.success(request, f'✓ Đã {status} deal "{deal.title}"')
    return redirect('admin_deals')


@login_required(login_url='login')
@user_passes_test(is_admin, login_url='home')
def admin_deal_delete(request, deal_id):
    deal = get_object_or_404(WeekendDeal, id=deal_id)
    title = deal.title
    deal.delete()
    messages.success(request, f'✓ Đã xóa deal "{title}"')
    return redirect('admin_deals')

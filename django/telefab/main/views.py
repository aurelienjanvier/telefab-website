# This file uses the following encoding: utf-8 
from django.db.models import Q
from django.shortcuts import render_to_response, get_object_or_404, redirect
from django.core.exceptions import PermissionDenied
from django.template import RequestContext, Context, Template
from django.http import HttpResponse, Http404
from django.core import urlresolvers
from django.core.mail import send_mail
from django.views.decorators.csrf import csrf_exempt
from models import *
from datetime import date, datetime, timedelta
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from telefab.local_settings import WEBSITE_CONFIG, API_PASSWORD
from telefab.settings import SITE_URL, EMAIL_FROM, MAIN_PLACE_NAME
import math

@login_required
def edit_event(request, event_id=None):
	"""
	Create or edit an event
	"""
	# Only animators allowed
	if not request.user.get_profile().is_animator():
		raise PermissionDenied()
	# Get the event to edit or create a new event
	event = None
	is_new = False
	saving_errors = []
	if event_id is not None:
		event = get_object_or_404(Event, pk=event_id)
	else:
		event = Event()
		is_new = True
	# Render
	template_data = {
		'event': event,
		'is_new': is_new,
		'saving_errors': saving_errors,
		'hours': range(24)
	}
	return render_to_response("events/edit.html", template_data, context_instance = RequestContext(request))

# Equipments

def show_equipment_categories(request, choice=False):
	"""
	Shows the list of equipment categories,
	used as modal dialog if choice is true
	"""
	categories = EquipmentCategory.objects.order_by('name')
	# Render
	template_data = {
		'categories': categories,
		'choice': choice
	}
	return render_to_response("equipments/categories.html", template_data, context_instance = RequestContext(request))

def show_equipments(request, category=None, choice=False):
	"""
	Show a list of equipments available in the FabLab (filtered by category if not None),
	used as modal dialog if choice is true
	"""
	category_obj = None
	equipments = Equipment.objects.filter(quantity__gt = 0).order_by('name')
	if category is not None:
		category_obj = get_object_or_404(EquipmentCategory, slug = category)
		equipments = equipments.filter(category = category_obj)
	
	# Render
	template_data = {
		'category': category_obj,
		'equipments': equipments,
		'choice': choice
	}
	return render_to_response("equipments/show.html", template_data, context_instance = RequestContext(request))

# Equipment loans

@login_required
def edit_loan(request, loan_id=None):
	"""
	Allows users to edit a loan or create one
	"""
	# Get the loan to edit or create a new loan
	loan = None
	is_new = False
	is_owner = True
	saving_errors = []
	if loan_id is not None:
		loan = get_object_or_404(Loan, pk=loan_id)
		# Check that the user can edit this loan
		if loan.borrower != request.user:
			if not request.user.get_profile().is_animator() or loan.cancel_time:
				raise PermissionDenied()
			is_owner = False
		elif not loan.is_waiting() and not request.user.get_profile().is_animator():
			raise PermissionDenied()
	else:
		loan = Loan()
		loan.borrower = request.user
		loan.request_time = datetime.now()
		is_new = True
	# Check if some data has been sent
	if request.POST.get("action", None) == "save":
		# Set the return date
		scheduled_return_date = request.POST.get('scheduled_return_date', '')
		try:
			scheduled_return_date = datetime.strptime(scheduled_return_date, "%d/%m/%Y").date()
		except ValueError:
			scheduled_return_date = None
			saving_errors.append(u"la date de retour est invalide")
		if scheduled_return_date is not None:
			if scheduled_return_date < date.today():
				saving_errors.append(u"la date de retour ne peut pas être dans le passé")
			else:
				loan.scheduled_return_date = scheduled_return_date
		# Set the comment
		loan.comment = request.POST.get('comment', '')
		# Browse equipments
		i = 0
		to_save = []
		to_delete = []
		bookings_count = 0
		equipments = []
		if not is_new:
			equipments = list(loan.equipments.all())
		while request.POST.get("equipment_id" + str(i+1), None) is not None:
			i = i + 1
			try:
				booking_id = int(request.POST.get("equipment_booking_id" + str(i), ""))
			except ValueError:
				booking_id = 0
			booking = None
			if booking_id != 0:
				try:
					booking = loan.bookings.get(pk = booking_id)
				except Booking.DoesNotExist:
					saving_errors.append(u"une réservation modifiée n'existe pas")
					continue
			remove_request = False
			try:
				remove_request = int(request.POST.get("equipment_remove" + str(i), "0")) > 0
			except:
				pass
			if remove_request:
				# This booking has been deleted
				if booking is not None:
					to_delete.append(booking)
					equipments.remove(booking.equipment)
			else:
				try:
					quantity = int(request.POST.get("equipment_quantity" + str(i), ""))
				except ValueError:
					quantity = 0
				if booking is not None:
					bookings_count = bookings_count + 1
					# This booking exists: edit the quantity
					if quantity <= 0 or quantity > booking.equipment.available_quantity(loan):
						saving_errors.append(u"il n'y a que " + str(booking.equipment.available_quantity(loan)) + " exemplaire(s) de " + booking.equipment.name)
						continue
					if quantity != booking.quantity:
						booking.quantity = quantity
						to_save.append(booking)
				else:
					# New booking
					# Get the equipment
					equipment_id = -1
					try:
						equipment_id = int(request.POST.get("equipment_id" + str(i), ""))
					except ValueError:
						pass
					if equipment_id == 0:
						# If the equipment id is 0, this is an empty field to ignore
						continue
					# Count this booking (if error, saving won't end)
					bookings_count = bookings_count + 1
					try:
						equipment = Equipment.objects.get(pk = int(request.POST.get("equipment_id" + str(i), "")))
					except (Equipment.DoesNotExist, ValueError):
						saving_errors.append(u"l'équipement \"" + request.POST.get("equipment_name" + str(i), "") + "\" n'existe pas")
						continue
					# Check that this equipment is not already booked
					if equipment in equipments:
						saving_errors.append(u"pour réserver plusieurs exemplaires d'un équipement, utilisez le champ \"quantité\"")
						continue
					# Check the quantity
					if quantity <= 0 or quantity > equipment.available_quantity(loan):
						saving_errors.append("il n'y a que " + str(equipment.available_quantity(loan)) + " exemplaire(s) de " + equipment.name)
						continue
					# Create
					booking = EquipmentLoan(loan = loan, equipment = equipment, quantity = quantity)
					to_save.append(booking)
					equipments.append(equipment)
		# Check that there is at least 1 booking
		if bookings_count == 0:
			saving_errors.append(u"au moins un équipement doit être réservé")
		if not saving_errors:
			# Save all if no error
			loan.save()
			for booking in to_delete:
				booking.delete()
			for booking in to_save:
				booking.loan_id = loan.id
				booking.save()
			if is_new:
				# Send an email to all animators about the new request
				body = unicode(request.user.get_profile()) + u" a fait une demande de matériel au Téléfab (retour prévu le " + unicode(loan.scheduled_return_date.strftime("%d/%m/%Y")) + u") :\n"
				for booking in loan.bookings.all():
					body = body + u"* " + unicode(booking.equipment)
					if booking.quantity > 1:
						body = body + u" (" + unicode(booking.quantity) + u")"
					body = body + "\n"
				body = body + u"\nPour voir toutes les demandes, allez ici : " + unicode(SITE_URL + urlresolvers.reverse('main.views.show_all_loans'))
				for animator in UserProfile.get_animators():
					send_mail(u"[Téléfab] Demande de matériel : " + unicode(request.user.get_profile()), body, EMAIL_FROM, [animator.email])
			# Redirect
			return redirect(urlresolvers.reverse('main.views.show_loans'))
	# Render
	all_equipments = Equipment.objects.filter(quantity__gt = 0)
	equipments = []
	for equipment in all_equipments:
		# Filter out unavailable equipments
		if equipment.available_quantity(loan) > 0:
			equipments.append({
				'object': equipment,
				'quantity': equipment.available_quantity(loan)
			})
	template_data = {
		'loan': loan,
		'is_new': is_new,
		'is_owner': is_owner,
		'equipments': equipments,
		'saving_errors': saving_errors
	}
	return render_to_response("loans/edit.html", template_data, context_instance = RequestContext(request))

@login_required
def show_loans(request):
	"""
	Show all the current loans of an user
	"""
	# Loans finished less than 7 days ago
	last_time = datetime.now() - timedelta(days=7)
	loans = request.user.loans.filter(cancel_time = None, return_time = None).order_by('-request_time')
	old_loans = request.user.loans.filter((Q(cancel_time = None) & Q(return_time__gte = last_time)) | (Q(return_time = None) & Q(cancel_time__gte = last_time))).order_by('-request_time')
	template_data = {
		'loans': loans,
		'old_loans': old_loans
	}
	return render_to_response("loans/show.html", template_data, context_instance = RequestContext(request))

@login_required
def show_all_loans(request):
	"""
	Show all loans to animators
	"""
	# Only animators
	if not request.user.get_profile().is_animator():
		raise PermissionDenied()
	# Loans finished less than 7 days ago
	last_time = datetime.now() - timedelta(days=7)
	loans = Loan.objects.filter(cancel_time = None, return_time = None).order_by('-request_time')
	old_loans = Loan.objects.filter((Q(cancel_time = None) & Q(return_time__gte = last_time)) | (Q(return_time = None) & Q(cancel_time__gte = last_time))).order_by('-request_time')
	template_data = {
		'loans': loans,
		'old_loans': old_loans,
		'animator': True
	}
	return render_to_response("loans/show.html", template_data, context_instance = RequestContext(request))

@login_required
def manage_loan(request, loan_id, action, value):
	"""
	Allows animators to manage loans: cancel or confirm
	"""
	# Only animators
	loan = get_object_or_404(Loan, pk=loan_id)
	if action == "cancel":
		# Manage the cancel status
		if (loan.borrower != request.user or loan.loan_time) and not request.user.get_profile().is_animator():
			raise PermissionDenied()
		if value == "1":
			loan.cancel_time = datetime.now()
			loan.cancelled_by = request.user
		else:
			loan.cancel_time = None
	elif action =="return":
		# Manage the return status
		if not request.user.get_profile().is_animator():
			raise PermissionDenied()
		if loan.cancel_time or not loan.loan_time:
			raise PermissionDenied()
		if value == "1":
			loan.return_time = datetime.now()
		else:
			loan.return_time = None
	elif action == "confirm":
		# Manage the confirm status
		if not request.user.get_profile().is_animator():
			raise PermissionDenied()
		if loan.cancel_time:
			raise PermissionDenied()
		if value == "1":
			loan.loan_time = datetime.now()
			loan.lender = request.user
		else:
			loan.loan_time = None
	else:
		raise Http404()
	loan.save()
	if request.user.get_profile().is_animator():
		return redirect(urlresolvers.reverse('main.views.show_all_loans'))
	else:
		return redirect(urlresolvers.reverse('main.views.show_loans'))

# Account

@login_required
def welcome(request):
	"""
	Welcome page of the lab
	"""
	# If the user has not filled its profile: ask (only once)
	if (not request.user.first_name or not request.user.last_name) and not request.session.get("already_welcome", False):
		request.session["already_welcome"] = True
		return redirect(urlresolvers.reverse('main.views.profile') + "?first_edit=1")
	template_data = {
		'telefab_open': Place.get_main_place().now_open,
		'api_password': API_PASSWORD
	}
	return render_to_response("account/welcome.html", template_data, context_instance = RequestContext(request))

def connection(request):
	"""
	Allows to register or log in
	"""
	if request.user.is_authenticated():
		return redirect(urlresolvers.reverse('main.views.welcome'))
	else:
		template_data = {
			'next': request.REQUEST.get('next', '')
		}
		return render_to_response("account/connection.html", template_data, context_instance = RequestContext(request))

def disconnect(request):
	"""
	Log the user out
	"""
	logout(request)
	return render_to_response("account/disconnect.html", context_instance = RequestContext(request))

def blog(request):
	"""
	Simple page to allow to log in on the blog.
	If the user seems to be logged in, send directly
	"""
	if request.user.is_authenticated() and request.user.get_profile().is_blog_user():
		# Persona logged in users will be detected and managed by wordpress
		return redirect('/wp-admin')
	return render_to_response("account/blog.html", context_instance = RequestContext(request))

@login_required
def profile(request):
	"""
	Modify the user profile
	"""
	# Did the user get sent here automatically at login?
	first_edit = request.REQUEST.get('first_edit', False)
	# Get data if any
	first_name = request.POST.get('first_name', None)
	last_name = request.POST.get('last_name', None)
	just_saved = False
	if first_name is not None and last_name is not None :
		request.user.first_name = first_name
		request.user.last_name = last_name
		request.user.save()
		just_saved = True
		if first_edit:
			# Go home if first time
			return redirect(urlresolvers.reverse('main.views.welcome'))
	template_data = {
		'first_edit': first_edit,
		'just_saved': just_saved
	}
	return render_to_response("account/profile.html", template_data, context_instance = RequestContext(request))

# Announcement screens

def announcements(request):
	"""
	Page displayed on announcement screens.
	Showing latest news and status
	"""
	# Select announcements that are visible, and with the right opening setting
	current_open = 'CLOSED'
	if Place.get_main_place().now_open():
		current_open = 'OPEN'
	announcements = Announcement.objects.filter(visible = True, opening__in = ('ANY', current_open))
	# Search for the first non-naked non-permanent announcement
	first_event = -1
	counter = 0
	for announcement in announcements:
		if not announcement.naked and not announcement.permanent:
			first_event = counter
			break
		counter+= 1
	# Display
	template_data = {
		'announcements': announcements,
		'first_event': first_event
	}
	return render_to_response("announcements/show.html", template_data, context_instance = RequestContext(request))

# Places

@login_required
def update_place(request):
	"""
	Updates a place to switch it between open or not.
	This is quite dirty for now (only the main place is supported)
	"""
	# Only animators
	if not request.user.get_profile().is_animator():
		raise PermissionDenied()
	# Update the place
	place = Place.get_main_place()
	if place.now_open():
		place.do_close_now()
	else:
		place.do_open_now(request.user)
	return redirect(urlresolvers.reverse('main.views.welcome'))

def update_place_mobile(request, password):
	"""
	Mobile page to update the place opening
	"""
	if password != API_PASSWORD:
		raise PermissionDenied()
	place = Place.get_main_place()
	user = None
	if request.user.is_authenticated():
		user = request.user
	if request.POST.get('action', None) == "switch":
		if place.now_open():
			place.do_close_now()
		else:
			place.do_open_now(user)
	template_data = {
		'place': place,
		'api_password': API_PASSWORD
	}
	return render_to_response("mobile/place.html", template_data, context_instance = RequestContext(request))


@csrf_exempt
def update_place_api(request):
	"""
	Update the place opening (used as API)
	"""
	# Check the hard-coded password
	if request.REQUEST.get('password', None) != API_PASSWORD:
		raise PermissionDenied()
	place = Place.get_main_place()
	# Should the place be opened or closed, or is it just a check?
	to_open = request.POST.get('open', None)
	if to_open == '1':
		place.do_open_now()
	elif to_open == '0':
		place.do_close_now()
	elif place.now_open():
		return HttpResponse("OPEN", content_type="text/plain")
	else:
		return HttpResponse("CLOSED", content_type="text/plain")
	return HttpResponse("OK", content_type="text/plain")
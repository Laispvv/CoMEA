"""
URL configuration for comea_website project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from comea_app.views import *
from django.conf.urls.static import static
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.conf.urls.i18n import i18n_patterns

urlpatterns = [
    path("admin/", admin.site.urls),
    path('i18n/', include('django.conf.urls.i18n')),
]

urlpatterns += i18n_patterns(
    path('', index, name='index'),
    path('concept_map/', concept_map, name='concept_map'),
    path('dashboard/', dashboard, name='dashboard'),
    path('select_rubric/', select_rubric, name='select_rubric'),
    path('delete_rubric/', delete_rubric, name='delete_rubric'),
    path('delete_concept_map/', delete_concept_map, name='delete_concept_map'),
    path('print_rubric/<int:rubric_id>/', print_rubric, name='print_rubric'),
    path('print_feedback/<int:concept_map_id>/', print_feedback, name='print_feedback'),
    path('file_upload_with_rubric/<int:rubric_id>/', file_upload_with_rubric, name='file_upload_with_rubric'),
    path('contact/', contact, name='contact'),
    path('privacy/', privacy, name='privacy'),
    path('evaluation_history/', evaluation_history, name='evaluation_history'),
    path('evaluation_rubric_config/', evaluation_rubric_config, name='evaluation_rubric_config'),
    path('topics_extraction_config/', topics_extraction_config, name='topics_extraction_config'),
    path('propositions_extraction_config/', propositions_extraction_config, name='propositions_extraction_config'),
    path('evaluation_loading/', evaluation_loading, name='evaluation_loading'),
    path('evaluation_config/<int:rubric_id>/<int:concept_map_id>/', evaluation_config, name='evaluation_config'),
    path('evaluation_save/', evaluation_save, name='evaluation_save'),
    path('recalculate_final_score/', recalculate_final_score, name='recalculate_final_score'),
    path('generate_comments_structure_colors/', generate_comments_structure_colors, name='generate_comments_structure_colors'),
    path('save_proposition_changes/', save_proposition_changes, name='save_proposition_changes'),
    path('save_topological_values/', save_topological_values, name='save_topological_values'),
    path('check_evaluation_status/', check_evaluation_status, name='check_evaluation_status'),
    path('process_evaluation_background/', process_evaluation_background, name='process_evaluation_background'),
    path('process_topics_extraction/', process_topics_extraction, name='process_topics_extraction'),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='index'), name='logout'),
    path('register/', register, name='register'),
) + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
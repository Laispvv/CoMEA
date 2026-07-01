from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from .models import *


class SoftDeleteAdminMixin:
    def get_queryset(self, request):
        queryset = getattr(self.model, 'all_objects', self.model._default_manager).all()
        return queryset

@admin.register(RubricConfiguration)
class RubricConfigurationAdmin(SoftDeleteAdminMixin, SimpleHistoryAdmin):
    list_display = ('id','name', 'user', 'proposition_rubric_checkbox', 'topological_scoring_rubric_checkbox', 'structure_classification', 'visual_aspects', 'created_at')
    history_list_display = ['name', 'user', 'is_deleted']
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'user', 'reference_document')
        }),
        ('Rubric Selection', {
            'fields': (
                'proposition_rubric_checkbox',
                'topological_scoring_rubric_checkbox',
                'structure_classification',
                'visual_aspects',
                'half_correct'
            )
        }),
        ('Weights', {
            'fields': (
                'proposition_weight', 
                'topological_scoring_weight'
            )
        }),
        ('Topological Configuration', {
            'fields': ('topological_level_configuration',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    search_fields = ['name', 'user__username']
    list_filter = ('user', 'proposition_rubric_checkbox', 'topological_scoring_rubric_checkbox', 'structure_classification', 'visual_aspects', 'created_at', 'is_deleted')
    readonly_fields = ('created_at', 'updated_at', 'is_deleted', 'deleted_at')
    date_hierarchy = 'created_at'

@admin.register(ConceptMap)
class ConceptMapAdmin(SoftDeleteAdminMixin, SimpleHistoryAdmin):
    list_display = ('id','filename', 'user', 'total_nodes', 'total_edges', 'modified', 'created_at')
    history_list_display = ['filename', 'user', 'is_deleted']
    search_fields = ['user__username', 'filename']
    list_filter = ('modified', 'created_at', 'user', 'is_deleted')
    readonly_fields = ('created_at', 'updated_at', 'total_nodes', 'total_edges', 'is_deleted', 'deleted_at')
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'filename', 'modified')
        }),
        ('Map Data', {
            'fields': (
                'nodes_data',
                'edges_data',
                'node_styles_data',
                'edge_styles_data',
                'propositions',
                'total_nodes',
                'total_edges',
                'concept_map_image_data'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'is_deleted', 'deleted_at'),
            'classes': ('collapse',)
        })
    )
    date_hierarchy = 'created_at'
    
@admin.register(CMEvaluationResult)
class CMEvaluationResultAdmin(SoftDeleteAdminMixin, SimpleHistoryAdmin):
    list_display = ('id', 'concept_map', 'user', 'evaluation_status', 'final_score', 'student_name', 'created_at')
    history_list_display = ['user', 'evaluation_status', 'final_score', 'is_deleted']
    list_filter = ('evaluation_status', 'created_at', 'user', 'modified', 'is_deleted')
    search_fields = ['user__username', 'concept_map__filename', 'student_name']
    readonly_fields = ('created_at', 'updated_at', 'is_deleted', 'deleted_at')
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'concept_map', 'rubric', 'student_name', 'modified')
        }),
        (
            'Student Information', {
                'fields': ('student_suspected_dyslexia', 'student_other_disabilities')
        }),
        ('Status', {
            'fields': ('evaluation_status',)
        }),
        ('Rubric Results', {
            'fields': ('proposition_rubric', 'topological_rubric', 'structure_rubric')
        }),
        ('Final Results', {
            'fields': ('final_score',)
        }),
        ('Comments', {
            'fields': ('comments_proposition', 'comments_topological_scoring', 'comments_structure_classification', 'comments_visual_aspects'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'is_deleted', 'deleted_at'),
            'classes': ('collapse',)
        })
    )
    date_hierarchy = 'created_at'
    
@admin.register(ReferenceDocument)
class ReferenceDocumentAdmin(SoftDeleteAdminMixin, SimpleHistoryAdmin):
    list_display = ('id', 'filename', 'user', 'modified', 'created_at')
    history_list_display = ['filename', 'user', 'is_deleted']
    list_filter = ('modified', 'created_at', 'user', 'is_deleted')
    search_fields = ['user__username', 'filename']
    readonly_fields = ('created_at', 'updated_at', 'is_deleted', 'deleted_at')
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'filename', 'modified')
        }),
        ('Content', {
            'fields': ('full_text',),
            'classes': ('collapse',)
        }),
        ('Extracted Data', {
            'fields': ('nodes_data', 'edges_data', 'propositions', 'propositions_weight')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'is_deleted', 'deleted_at'),
            'classes': ('collapse',)
        })
    )
    date_hierarchy = 'created_at'

@admin.register(PropositionRubric)
class PropositionRubricAdmin(SoftDeleteAdminMixin, SimpleHistoryAdmin):
    list_display = ('id', 'user', 'concept_map', 'proposition_score', 'completeness_score', 'correctness_score', 'modified', 'created_at')
    history_list_display = ['user', 'proposition_score', 'is_deleted']
    list_filter = ('modified', 'created_at', 'user', 'is_deleted')
    search_fields = ['user__username', 'concept_map__filename']
    readonly_fields = ('created_at', 'updated_at', 'is_deleted', 'deleted_at')
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'concept_map', 'evaluation_result', 'modified')
        }),
        ('Classifications', {
            'fields': ('propositions_classification', 'propositions_correction'),
            'classes': ('collapse',)
        }),
        ('Scores', {
            'fields': (
                'completeness_score', 'completeness_specification',
                'correctness_score', 'proposition_specification',
                'proposition_score'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'is_deleted', 'deleted_at'),
            'classes': ('collapse',)
        })
    )
    date_hierarchy = 'created_at'
    
@admin.register(TopologicalScoringRubric)
class TopologicalScoringRubricAdmin(SoftDeleteAdminMixin, SimpleHistoryAdmin):
    list_display = ('id', 'user', 'concept_map', 'topological_score', 'topological_score_normalized', 'modified', 'created_at')
    history_list_display = ['user', 'topological_score', 'is_deleted']
    list_filter = ('modified', 'created_at', 'user', 'topological_score', 'is_deleted')
    search_fields = ['user__username', 'concept_map__filename']
    readonly_fields = ('created_at', 'updated_at', 'is_deleted', 'deleted_at')
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'concept_map', 'evaluation_result', 'modified')
        }),
        ('Metrics', {
            'fields': (
                'long_text_nodes',
                'edges_without_link_word',
                'branching_points', 
                'hierarchical_depth',
                'cross_links'
            )
        }),
        ('Scores', {
            'fields': ('topological_score', 'topological_score_normalized')
        }),
        ('Descriptions', {
            'fields': (
                'long_text_nodes_description', 
                'edges_without_link_word_description',
                'cross_links_description', 
                'topological_score_description'
            ),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'is_deleted', 'deleted_at'),
            'classes': ('collapse',)
        })
    )
    date_hierarchy = 'created_at'
    
@admin.register(StructureClassificationRubric)
class StructureClassificationRubricAdmin(SoftDeleteAdminMixin, SimpleHistoryAdmin):
    list_display = ('id', 'user', 'concept_map', 'size_spokes_threshold', 'size_chain_threshold', 'modified', 'created_at')
    history_list_display = ['user', 'is_deleted']
    list_filter = ('modified', 'created_at', 'user', 'is_deleted')
    search_fields = ['user__username', 'concept_map__filename']
    readonly_fields = ('created_at', 'updated_at', 'is_deleted', 'deleted_at')
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'concept_map', 'evaluation_result', 'modified')
        }),
        ('Configuration', {
            'fields': (
                'size_spokes_threshold',
                'size_chain_threshold'
            )
        }),
        ('Results', {
            'fields': ('node_colors', 'description')
        }),
        ('Image', {
            'fields': ('concept_map_image',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'is_deleted', 'deleted_at'),
            'classes': ('collapse',)
        })
    )
    date_hierarchy = 'created_at'
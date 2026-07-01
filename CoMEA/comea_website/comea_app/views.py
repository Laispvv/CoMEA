import threading
import logging
import traceback

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.core.files.storage import FileSystemStorage
from .utils import *
import os
from django.conf import settings
from django.http import JsonResponse
import json
from .models import *
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.utils.translation import gettext as _

def index(request):
    return render(request, 'base.html')

def concept_map(request):
    return render(request, 'concept_map.html')

@login_required
def dashboard(request):
    """Main dashboard where users choose between creating a rubric or evaluating a map"""
    return render(request, 'dashboard.html')

@login_required
def select_rubric(request):
    """Display all rubrics for the user to select one for evaluation"""
    rubrics = RubricConfiguration.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'select_rubric.html', {'rubrics': rubrics})

@login_required
def delete_rubric(request):
    """Soft delete a rubric configuration by ID"""
    if request.method == 'POST':
        try:
            rubric_id = request.POST.get('rubric_id')
            
            if not rubric_id:
                return JsonResponse({'status': 'error', 'message': _('Missing rubric ID')}, status=400)
            
            rubric = RubricConfiguration.objects.filter(id=rubric_id, user=request.user).first()
            
            if not rubric:
                return JsonResponse({'status': 'error', 'message': _('Rubric not found')}, status=404)
            
            # Soft delete ao invés de delete físico
            rubric._change_reason = f'Soft deleted by user {request.user.username}'
            rubric.soft_delete()
            return JsonResponse({'status': 'success', 'message': _('Rubric deleted successfully')})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': _('Invalid method')}, status=405)

@login_required
def delete_concept_map(request):
    """Soft delete a concept map by ID"""
    if request.method == 'POST':
        try:
            concept_map_id = request.POST.get('concept_map_id')
            
            if not concept_map_id:
                return JsonResponse({'status': 'error', 'message': _('Missing concept map ID')}, status=400)
            
            concept_map = ConceptMap.objects.filter(id=concept_map_id, user=request.user).first()
            
            if not concept_map:
                return JsonResponse({'status': 'error', 'message': _('Concept map not found')}, status=404)
            
            # Soft delete ao invés de delete físico
            concept_map._change_reason = f'Soft deleted by user {request.user.username}'
            concept_map.soft_delete()
            
            # Soft delete das avaliações relacionadas
            evaluations = CMEvaluationResult.objects.filter(concept_map=concept_map, user=request.user)
            for evaluation in evaluations:
                evaluation._change_reason = f'Soft deleted in cascade with concept map {concept_map.filename}'
                evaluation.soft_delete()
            
            return JsonResponse({'status': 'success', 'message': _('Concept map deleted successfully')})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': _('Invalid method')}, status=405)

@login_required
def print_rubric(request, rubric_id):
    """Generate a printable page with rubric configuration details"""
    try:
        rubric = RubricConfiguration.objects.get(id=rubric_id, user=request.user)
    except RubricConfiguration.DoesNotExist:
        return redirect('select_rubric')
    
    # Get topological parameters if this rubric type is enabled
    topological_params = []
    if rubric.topological_scoring_rubric_checkbox:
        # Criar lista de tuplas (level, config) para facilitar iteração no template
        for level in range(7):
            topological_params.append((level, rubric.topological_level_configuration[str(level)]))
    
    context = {
        'rubric': rubric,
        'topological_params': topological_params,
        'now': datetime.now(),
    }
    
    return render(request, 'print_rubric.html', context)

@login_required
def print_feedback(request, concept_map_id):
    """Generate a printable page with concept map feedback details"""
    concept_map = get_object_or_404(ConceptMap, id=concept_map_id, user=request.user)
    
    try:
        evaluation = CMEvaluationResult.objects.filter(
            user=request.user,
            concept_map=concept_map
        ).select_related('rubric').latest('updated_at')
    except CMEvaluationResult.DoesNotExist:
        return redirect('evaluation_history')
    
    # Gerar imagem do grafo estrutural
    structure_graph_image = evaluation.structure_rubric.concept_map_image if evaluation.structure_rubric else None
    concept_map_image_src = concept_map.concept_map_image_data
    if concept_map_image_src:
        concept_map_image_src = concept_map_image_src.strip()
        if not concept_map_image_src.startswith('data:image'):
            concept_map_image_src = f'data:image/png;base64,{concept_map_image_src}'
    
    context = {
        'concept_map': concept_map,
        'rubric': evaluation.rubric,
        'evaluation': evaluation,
        'structure_graph_image': structure_graph_image,
        'concept_map_image_src': concept_map_image_src,
        'student_name': evaluation.student_name if evaluation.student_name else "-",
        'now': datetime.now(),
        'comments_proposition_list': evaluation.comments_proposition.split('\n') if evaluation.comments_proposition else [],
        'comments_topological_list': evaluation.comments_topological_scoring.split('\n') if evaluation.comments_topological_scoring else [],
        'comments_structure_classification_list': evaluation.comments_structure_classification.split('\n') if evaluation.comments_structure_classification else [],
        'comments_visual_aspects': evaluation.comments_visual_aspects if evaluation.comments_visual_aspects else "",
    }
    
    return render(request, 'print_feedback.html', context)

@login_required
def file_upload_with_rubric(request, rubric_id):
    """Upload concept map file to evaluate with a specific rubric"""
    rubric = get_object_or_404(RubricConfiguration, id=rubric_id, user=request.user)
    
    # Contar avaliações em andamento
    evaluations_in_progress = CMEvaluationResult.objects.filter(
        user=request.user,
        evaluation_status__in=['pending', 'processing']
    ).count()
    
    if request.method == 'POST':
        # Verificar quantas avaliações estão em andamento
        if evaluations_in_progress >= 3:
            return JsonResponse({
                'error': _('You already have 3 evaluations in progress. Please wait for them to complete before uploading a new concept map.')
            }, status=429)
        
        if not request.FILES:
            return JsonResponse({'error': _('No file uploaded')}, status=400)
        
        mapa = request.FILES.get('mapa')
        if not mapa:
            return JsonResponse({'error': _('No file uploaded')}, status=400)
        
        fs = FileSystemStorage()
        full_path = None
        node_styles = None
        edge_styles = None
        try:
            # Save and process the concept map
            mapa_path = fs.save(mapa.name, mapa)
            full_path = os.path.join(settings.MEDIA_ROOT, mapa_path)
            if full_path.lower().endswith('.excalidraw'):
                nodes, edges, node_styles, edge_styles = extract_proposition_from_map_with_style(full_path)
            elif full_path.lower().endswith('.cxl'):
                nodes, edges, node_styles, edge_styles = extract_proposition_from_map_cmaptools(full_path)
            else:
                return JsonResponse({'error': _('Unsupported file type. Please upload .excalidraw or .cxl files.')}, status=400)
            
            # Create concept map object
            concept_map = ConceptMap.objects.create(
                user=request.user,
                filename=mapa.name,
                nodes_data=nodes,
                edges_data=[list(edge) for edge in edges],
                node_styles_data=node_styles,
                edge_styles_data=edge_styles
            )
            concept_map._change_reason = f'Uploaded concept map file: {mapa.name}'
            concept_map.save()
            
            # Store rubric and concept map IDs in session
            request.session['rubric_id'] = rubric.id
            request.session['concept_map_id'] = concept_map.id
            
            # Clean up uploaded file
            if os.path.exists(full_path):
                os.remove(full_path)
            
            # Redirect to evaluation loading
            return redirect('propositions_extraction_config')
            
        except Exception as e:
            if full_path and os.path.exists(full_path):
                os.remove(full_path)
            return JsonResponse({'error': str(e)}, status=500)
    
    return render(request, 'file_upload_with_rubric.html', {
        'rubric': rubric,
        'evaluations_in_progress': evaluations_in_progress
    })

def contact(request):
    return render(request, 'contact.html')

def terms_of_use(request):
    return render(request, 'terms_of_use.html')

def privacy(request):
    return render(request, 'privacy.html')

def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('login')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})

@login_required
def evaluation_rubric_config(request):
    if request.method == 'POST':
        try:
            # Criar configuração de rubrica
            # Gerar nome com timestamp
            timestamp_name = datetime.now().strftime("%Y%m%d_%H%M%S")
            rubric_name = request.POST.get('rubric_name', '').strip()
            final_name = rubric_name if rubric_name else f"rubric_{timestamp_name}"
            
            rubric_configuration = RubricConfiguration.objects.create(
                name=final_name,
                user=request.user,
                topological_scoring_rubric_checkbox = True if request.POST.get('topological_scoring_checkbox') == 'on' else False,
                proposition_rubric_checkbox = True if request.POST.get('proposition_analysis_checkbox') == 'on' else False,
                half_correct = True if request.POST.get('half_correct_feedback') == 'on' else False,
                structure_classification = True if request.POST.get('structure_classification_checkbox') == 'on' else False,
                visual_aspects = True if request.POST.get('visual_aspects_checkbox') == 'on' else False,
                topological_scoring_weight=int(request.POST.get('topological_scoring_weight') or 1),
                proposition_weight=int(request.POST.get('proposition_analysis_weight') or 1)
            )
            
            rubric_configuration._change_reason = f'Created by user {request.user.username}'
            rubric_configuration.save()
            
            
            # Criar TopologicalScoringRubric template com parâmetros configurados
            if rubric_configuration.topological_scoring_rubric_checkbox:
                # Iterar pelos níveis 0-6 e configurar cada um dinamicamente
                all_levels_config = {}
                for level in range(7):
                    level_config = {
                        'branching': request.POST.get(f'level{level}_branching') or 0,
                        'hierarchy': request.POST.get(f'level{level}_hierarchy') or 0,
                        'crosslinks': request.POST.get(f'level{level}_crosslinks') or 0,
                        'concept_long_text': request.POST.get(f'level{level}_clt') or 0,
                        'linking_phrase': request.POST.get(f'level{level}_lp') or 0,
                    }
                    all_levels_config[level] = level_config
                    
                rubric_configuration.topological_level_configuration = all_levels_config
                rubric_configuration._change_reason = 'Added topological level configuration'
                rubric_configuration.save()
            
            reference_adv = request.FILES.get('reference_adv')
            reference_qr = request.FILES.get('reference_qr')
            reference = reference_adv if reference_adv else reference_qr

            # Processar documento de referência se fornecido
            if reference:
                # Validar tipo MIME
                if reference.content_type != 'application/pdf' or not reference.name.lower().endswith('.pdf'):
                    return JsonResponse({
                        'status': 'error',
                        'message': _('Invalid file type. Please select a PDF file.')
                    }, status=400)
                
                fs = FileSystemStorage()
                reference_filename = fs.save(reference.name, reference)
                reference_path = fs.path(reference_filename)
                    
                try:
                    reference_text = extract_text_from_pdf(reference_path)
                    
                    if len(reference_text.split()) > 0:
                        reference_document = ReferenceDocument.objects.create(
                            user=request.user,
                            filename=reference.name,
                            full_text=reference_text,
                        )
                        reference_document._change_reason = f'Uploaded reference document: {reference.name}'
                        reference_document.save()
                        
                        # Salvar IDs na sessão para processamento posterior
                        request.session['reference_document_id'] = reference_document.id
                        request.session['rubric_id'] = rubric_configuration.id
                        request.session['processing_topics'] = True
                        
                        # Redirecionar para tela de loading
                        return redirect('evaluation_loading')
                finally:
                    # Remover arquivo temporário
                    if os.path.exists(reference_path):
                        os.remove(reference_path)
            
            return redirect('dashboard')
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    
    level_description, structural_meaning = topological_level_descriptions()
    return render(request, 'evaluation_rubric_config.html', {
        'level_description': level_description,
        'structural_meaning': structural_meaning
    })

def topics_extraction_config(request):
    if request.method == 'POST':
        try:
            topics_data_json = request.POST.get('topics_data')
            propositions_weight_json = request.POST.get('propositions_weight')
            edges_data_json = request.POST.get('edges_data')
            nodes_data_json = request.POST.get('nodes_data')
            
            if topics_data_json:
                topics_data = json.loads(topics_data_json)
                propositions_weight = json.loads(propositions_weight_json) if propositions_weight_json else {}
                edges_data = json.loads(edges_data_json) if edges_data_json else []
                nodes_data = json.loads(nodes_data_json) if nodes_data_json else []

                normalized_edges = []
                normalized_nodes = []

                for node in nodes_data:
                    node_text = str(node).strip()
                    if node_text and node_text not in normalized_nodes:
                        normalized_nodes.append(node_text)

                for edge in edges_data:
                    if not isinstance(edge, list) or len(edge) < 3:
                        continue

                    source = str(edge[0]).strip()
                    target = str(edge[1]).strip()
                    relation = str(edge[2]).strip()

                    if not source or not target or not relation:
                        continue

                    normalized_edges.append([source, target, relation])
                    if source not in normalized_nodes:
                        normalized_nodes.append(source)
                    if target not in normalized_nodes:
                        normalized_nodes.append(target)

                normalized_propositions = [f"{edge[0]} <{edge[2]}> {edge[1]}" for edge in normalized_edges]

                # Mantém pesos alinhados com as proposições normalizadas.
                normalized_weights = {}
                for proposition in normalized_propositions:
                    normalized_weights[proposition] = int(propositions_weight.get(proposition, 1) or 1)

                # Se não houver proposições normalizadas (caso legado), preserva os dados recebidos.
                if not normalized_propositions:
                    normalized_propositions = [str(topic).strip() for topic in topics_data if str(topic).strip()]
                    normalized_weights = {
                        topic: int(propositions_weight.get(topic, 1) or 1)
                        for topic in normalized_propositions
                    }
                
                # Atualizar o ReferenceDocument existente
                reference_document = ReferenceDocument.objects.filter(user=request.user).latest('updated_at')
                reference_document.nodes_data = normalized_nodes
                reference_document.edges_data = normalized_edges
                reference_document.propositions = normalized_propositions
                reference_document.propositions_weight = normalized_weights
                reference_document._change_reason = 'User updated nodes, edges, propositions and weights'
                reference_document.save()
                
                # Redirecionar para tela de loading ao invés de evaluation_config
                return redirect('dashboard')
            else:
                return JsonResponse({'status': 'error', 'message': 'Missing data'}, status=400)
                
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    # GET - mostrar tópicos para edição
    try:
        # Buscar o reference_document pelo ID armazenado na sessão
        reference_document_id = request.session.get('reference_document_id')
        if reference_document_id:
            # Buscar pelo ID específico passado via sessão
            reference_document = ReferenceDocument.objects.get(
                id=reference_document_id,
                user=request.user,
            )
        else:
            # Fallback: buscar o mais recente se não houver ID na sessão
            reference_document = ReferenceDocument.objects.filter(user=request.user).latest('updated_at')
        
        topics = reference_document.propositions or []
        concepts = reference_document.nodes_data or []
        edges = reference_document.edges_data or []
        proposition_weights = reference_document.propositions_weight or {}
        
        # Buscar o rubric_configuration mais recente para contexto
        rubric_configuration = RubricConfiguration.objects.filter(user=request.user).latest('updated_at')
    except ReferenceDocument.DoesNotExist:
        topics = []
        concepts = []
        edges = []
        proposition_weights = {}
        rubric_configuration = None
    except RubricConfiguration.DoesNotExist:
        rubric_configuration = None

    return render(request, 'topics_extraction_config.html', {
        'topics': topics,
        'concepts': concepts,
        'edges': edges,
        'proposition_weights': proposition_weights,
        'rubric_configuration': rubric_configuration
    })

def propositions_extraction_config(request):
    # Processar POST - salvar atualizações do grafo
    if request.method == 'POST':
        try:
            nodes_data_json = request.POST.get('nodes_data')
            edges_data_json = request.POST.get('edges_data')
            concept_map_image_base64 = request.POST.get('concept_map_image')
            
            if nodes_data_json and edges_data_json:
                nodes_data = json.loads(nodes_data_json)
                edges_data = json.loads(edges_data_json)

                rubric_id = request.session.get('rubric_id')
                concept_map_id = request.session.get('concept_map_id')

                if not rubric_id or not concept_map_id:
                    return JsonResponse({'status': 'error', 'message': 'Missing selected rubric or concept map in session'}, status=400)

                rubric = get_object_or_404(RubricConfiguration, id=rubric_id, user=request.user)
                
                # Atualizar o ConceptMap existente
                concept_map = get_object_or_404(ConceptMap, id=concept_map_id, user=request.user)
                concept_map.nodes_data = nodes_data
                concept_map.edges_data = edges_data
                if concept_map_image_base64:
                    normalized_image_data = concept_map_image_base64.strip()
                    if not normalized_image_data.startswith('data:image'):
                        normalized_image_data = f'data:image/png;base64,{normalized_image_data}'
                    concept_map.concept_map_image_data = normalized_image_data
                concept_map._change_reason = 'User updated nodes and edges data'
                concept_map.save()
                concept_map.create_propositions()
                
                # Criar CMEvaluationResult com status 'pending'
                evaluation = CMEvaluationResult.objects.create(
                    user=request.user,
                    concept_map=concept_map,
                    rubric=rubric,
                    evaluation_status='pending',
                    final_score=0.0
                )
                
                # Salvar IDs na sessão
                request.session['concept_map_id'] = concept_map.id
                request.session['evaluation_id'] = evaluation.id
                request.session['rubric_id'] = rubric.id
                
                # Redirecionar para evaluation_history
                return redirect('evaluation_history')
            else:
                return JsonResponse({'status': 'error', 'message': 'Missing data'}, status=400)
                
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    # GET - mostrar grafo para edição
    visual_aspects_enabled = False
    try:
        rubric_configuration = None
        rubric_id = request.session.get('rubric_id')
        if rubric_id:
            rubric_configuration = RubricConfiguration.objects.filter(
                id=rubric_id,
                user=request.user,
            ).first()

        if rubric_configuration is None:
            rubric_configuration = RubricConfiguration.objects.filter(user=request.user).latest('updated_at')

        concept_map_id = request.session.get('concept_map_id')
        if concept_map_id:
            concept_map = ConceptMap.objects.get(id=concept_map_id, user=request.user)
        else:
            concept_map = ConceptMap.objects.filter(user=request.user).latest('updated_at')
        nodes = concept_map.nodes_data
        edges = [tuple(e) for e in concept_map.edges_data]
        node_styles = concept_map.node_styles_data or {}
        edge_styles = concept_map.edge_styles_data or []
        visual_aspects_enabled = bool(rubric_configuration.visual_aspects)
        
        
        # Preparar dados do grafo para o Cytoscape.js
        # IMPORTANTE: nodes é um dict {node_id: text}
        graph_data = {
            'nodes': [
                {
                    'id': node_id,
                    'label': node_text,
                    'style': node_styles.get(node_id)
                }
                for node_id, node_text in nodes.items()
            ],
            'edges': [
                {
                    'source': edge[0],
                    'target': edge[1],
                    'label': edge[2] if len(edge) > 2 else '',
                    'style': edge_styles[idx] if idx < len(edge_styles) else None
                }
                for idx, edge in enumerate(edges)
            ]
        }
    except ConceptMap.DoesNotExist:
        graph_data = {'nodes': [], 'edges': []}
    except Exception as e:
        graph_data = {'nodes': [], 'edges': []}

    return render(request, 'propositions_extraction_config.html', {
        'has_concept_map': bool(graph_data['nodes']),
        'graph_data': graph_data,
        'visual_aspects_enabled': visual_aspects_enabled,
})

def _process_evaluation_logic(user, rubric_id, concept_map_id):
    """Lógica de processamento de avaliação independente do request"""
    logger = logging.getLogger(__name__)
    
    logger.info(f"Starting evaluation processing for rubric={rubric_id}, map={concept_map_id}")
    
    # Get rubric and concept map
    try:
        rubric = RubricConfiguration.objects.get(id=rubric_id, user=user)
        concept_map = ConceptMap.objects.get(id=concept_map_id, user=user)
    except (RubricConfiguration.DoesNotExist, ConceptMap.DoesNotExist) as e:
        logger.error(f"Rubric or ConceptMap not found: {e}")
        raise
    
    # Buscar ou criar CMEvaluationResult
    evaluation, created = CMEvaluationResult.objects.get_or_create(
        user=user,
        concept_map=concept_map,
        rubric=rubric,
        defaults={'final_score': 0.0}
    )
    
    # Atualizar status para 'processing'
    if evaluation.evaluation_status in ['pending', str(_('pending'))]:
        evaluation.evaluation_status = 'processing'
        evaluation._change_reason = 'Started evaluation processing'
        evaluation.save()
        logger.info(f"Evaluation status updated to 'processing': {evaluation.id}")
    
    evaluation.save()
    
    # Process topological rubric
    if rubric.topological_scoring_rubric_checkbox and not evaluation.topological_rubric:
        topological, created = TopologicalScoringRubric.objects.get_or_create(
            user=user,
            concept_map=concept_map,
            evaluation_result=evaluation
        )
        
        level_description, structural_meaning = topological_level_descriptions()
        topological.calculate_topological_score()
        topological.topological_score_description = {
            'level': str(level_description[str(topological.topological_score)]),
            'meaning': str(structural_meaning[str(topological.topological_score)]),
        }
        topological._change_reason = 'Initial topological score calculation'
        topological.save()
        evaluation.topological_rubric = topological
        evaluation._change_reason = 'Added topological rubric to evaluation'
        evaluation.save()
        logger.info("Topological score calculated")
    
    # Process proposition rubric
    if rubric.proposition_rubric_checkbox and not evaluation.proposition_rubric:
        proposition_rubric, created = PropositionRubric.objects.get_or_create(
            user=user,
            concept_map=concept_map,
            evaluation_result=evaluation,
        )
        
        with ThreadPoolExecutor(max_workers=6) as executor:
            # Submeter ambas as tarefas
            future_classification = executor.submit(
                classify_propositions,
                concept_map.propositions,
                rubric.reference_document.propositions
            )
            
            future_correction = executor.submit(
                correct_propositions,
                concept_map.propositions,
                rubric.reference_document.full_text
            )
            
            # Aguardar resultados com timeout de 300 segundos
            try:
                classification = future_classification.result(timeout=300)
                correction = future_correction.result(timeout=300)
                
            except TimeoutError:
                logger.error("Timeout ao processar proposições")
                raise Exception("Timeout ao processar proposições")
            except Exception as e:
                logger.error(f"Error processing propositions: {e}")
                raise
        
        # Atribuir resultados
        proposition_rubric.propositions_classification = classification
        proposition_rubric.propositions_correction = correction
        
        # Salvar primeiro para estabelecer foreign keys
        proposition_rubric._change_reason = 'Initial proposition classification and correction'
        proposition_rubric.save()
        
        # Agora calcular scores (que fazem save internamente)
        proposition_rubric.calculate_proposition_score()
        proposition_rubric._change_reason = 'Calculated proposition scores'
        proposition_rubric.save()
        
        evaluation.proposition_rubric = proposition_rubric
        evaluation._change_reason = 'Added proposition rubric to evaluation'
        evaluation.save()
        logger.info("Propositions processed")
        
    # Process structure classification
    if rubric.structure_classification and not evaluation.structure_rubric:
        structure_classification, created = StructureClassificationRubric.objects.get_or_create(
            user=user,
            concept_map=concept_map,
            evaluation_result=evaluation,
            description={}
        )
        
        structure_classification.color_map()
        evaluation.structure_rubric = structure_classification
        evaluation._change_reason = 'Added structure classification rubric to evaluation'
        evaluation.save()
        
        suggested_relations = []
        if rubric.proposition_rubric_checkbox:
            if 'conexoes_faltantes' not in structure_classification.description:
                
                with ThreadPoolExecutor(max_workers=3) as executor:
                    # Submeter ambas as tarefas
                    future_suggestion = executor.submit(
                        find_missing_relations,
                        concept_map.edges_data,
                        concept_map.nodes_data,
                        rubric.reference_document.propositions
                    )
                    # Aguardar resultados com timeout de 300 segundos
                    try:
                        suggested_relations = future_suggestion.result(timeout=300)
                        
                    except TimeoutError:
                        logger.error("Timeout ao processar proposições")
                        raise Exception("Timeout ao processar proposições")
                    except Exception as e:
                        logger.error(f"Error processing propositions: {e}")
                        raise
            else:
                suggested_relations = structure_classification.description
            structure_classification.description = suggested_relations
            structure_classification._change_reason = 'Added suggested relations and concepts to structure classification'
            structure_classification.save()

        # Garante uma imagem inicial para o relatório de impressão.
        if not structure_classification.concept_map_image and concept_map.concept_map_image_data:
            initial_image = concept_map.concept_map_image_data.strip()
            if initial_image.startswith('data:image') and ',' in initial_image:
                initial_image = initial_image.split(',', 1)[1]
            structure_classification.concept_map_image = initial_image
            structure_classification._change_reason = 'Initialized structure image from concept map snapshot'
            structure_classification.save()
        logger.info("Structure classified")
    
    if created:
        evaluation.save()
    
    # Sempre calcular final score antes de marcar como completo
    evaluation.calculate_final_score()
    logger.info(f"Final score calculated: {evaluation.final_score}")
    
    # Marcar como completa
    if evaluation.evaluation_status == 'processing':
        evaluation.evaluation_status = 'completed'
        evaluation._change_reason = 'Evaluation completed successfully'
        evaluation.save()
        logger.info(f"✅ Evaluation marked as completed (ID: {evaluation.id}, Score: {evaluation.final_score})")
    else:
        logger.warning(f"⚠️ Evaluation status is '{evaluation.evaluation_status}', not 'processing'. Expected 'processing'.")
    
    logger.info("Evaluation completed successfully")
    return evaluation

@login_required
def evaluation_config(request, rubric_id, concept_map_id):
    # Check if we have rubric and concept map IDs in session (from file_upload_with_rubric flow)
    structure_graph_data = {}
    
    if rubric_id and concept_map_id:
        rubric = get_object_or_404(RubricConfiguration, id=rubric_id, user=request.user)
        concept_map = get_object_or_404(ConceptMap, id=concept_map_id, user=request.user)
        
        # Clear session data (verificar se existe primeiro)
        if hasattr(request, 'session'):
            request.session.pop('rubric_id', None)
            request.session.pop('concept_map_id', None)
    else:
        # Normal flow - use latest rubric and concept map
        try:
            rubric = RubricConfiguration.latest_for_user(request.user)
        except RubricConfiguration.DoesNotExist:
            return redirect('evaluation_rubric_config')

        try:
            concept_map = ConceptMap.latest_for_user(request.user)
        except ConceptMap.DoesNotExist:
            return redirect('dashboard')
    
    # Processar avaliação usando a função auxiliar
    try:
        evaluation = _process_evaluation_logic(request.user, rubric.id, concept_map.id)
    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    # Preparar dados do grafo estrutural
    if evaluation.structure_rubric:
        structure_graph_data = prepare_structure_graph_data(evaluation.structure_rubric,
                                                            concept_map,
                                                            evaluation.structure_rubric.description)

    concept_map_image_src = concept_map.concept_map_image_data
    if concept_map_image_src:
        concept_map_image_src = concept_map_image_src.strip()
        if not concept_map_image_src.startswith('data:image'):
            concept_map_image_src = f'data:image/png;base64,{concept_map_image_src}'

    context = {
        'rubric': rubric,
        'evaluation': evaluation,
        'concept_map': concept_map,
        'concept_map_image_src': concept_map_image_src,
        'structure_graph_data': structure_graph_data,
    }

    return render(request, 'evaluation_config.html', context)

@login_required
def recalculate_final_score(request):
    if request.method == 'POST':
        try:
            evaluation_id = request.POST.get('evaluation_id')
            concept_map_id = request.POST.get('concept_map_id')
            evaluation = get_object_or_404(CMEvaluationResult, id=evaluation_id,
                                           user=request.user, concept_map=concept_map_id)
            
            proposition_weight = evaluation.rubric.proposition_weight if evaluation.rubric.proposition_rubric_checkbox else 0
            topological_weight = evaluation.rubric.topological_scoring_weight if evaluation.rubric.topological_scoring_rubric_checkbox else 0
            scores = request.POST.get('scores')
            
            if scores:
                scores_data = json.loads(scores)
                proposition_score = float(scores_data.get('proposition_final_score', 0))
                topological_score = float(scores_data.get('topological_final_score', 0))
                total_weight = proposition_weight + topological_weight
                
                if total_weight > 0:
                    weighted_proposition = (proposition_score * proposition_weight) if proposition_weight > 0 else 0
                    weighted_topological = (topological_score * topological_weight) if topological_weight > 0 else 0
                    recalculated_score = round(((weighted_proposition + weighted_topological) / total_weight), 2)
                else: 
                    recalculated_score = 0.0
            
            return JsonResponse({'status': 'success', 'recalculated_score': recalculated_score})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
def evaluation_save(request):
    """Salva todas as alterações das proposições (classificação, justificação e tópicos)"""
    if request.method == 'POST':
        try:
            infos_save_json = request.POST.get('infos_save')
            topological_json = request.POST.get('topological_data')
            structure_json = request.POST.get('structure_data')
            proposition_json = request.POST.get('propositions_data')
            proposition_scores_json = request.POST.get('proposition_scores')
            evaluation_id = request.POST.get('evaluation_id')
            concept_map_id = request.POST.get('concept_map_id')
            user = request.user
            
            evaluation = get_object_or_404(CMEvaluationResult, id=evaluation_id, user=user, concept_map=concept_map_id)
            
            change_reason_parts = []
            
            if structure_json:
                structure = json.loads(structure_json)
                structure_rubric = evaluation.structure_rubric
                
                node_colors = structure.get('updated_colors')
                base64_concept_map = structure.get('concept_map_image')
                added_connections = structure.get('added_connections')
                added_nodes = structure.get('added_nodes')
                
                # Verificar se houve mudanças reais antes de salvar
                structure_changed = False
                
                if node_colors != structure_rubric.node_colors:
                    structure_rubric.node_colors = node_colors
                    structure_rubric._change_reason = 'User modified the structure node colors'
                    structure_changed = True
                
                # Sempre atualizar a imagem estrutural no salvamento, quando disponível.
                if base64_concept_map:
                    normalized_structure_image = base64_concept_map.strip()
                    if normalized_structure_image.startswith('data:image') and ',' in normalized_structure_image:
                        normalized_structure_image = normalized_structure_image.split(',', 1)[1]
                    structure_rubric.concept_map_image = normalized_structure_image
                    structure_rubric._change_reason = 'Updated structure image on feedback save'
                    structure_changed = True
                
                if added_connections:
                    conexoes_faltantes = []
                    for conn in added_connections:
                        conexoes_faltantes.append({
                            "source":conn['source'],
                            "target":conn['target'],
                            "label":conn['label']
                        })
                    
                    description = {"new_nodes": added_nodes,
                                    "conexoes_faltantes": conexoes_faltantes}
                    if description != structure_rubric.description:
                        structure_rubric.description = description
                        structure_rubric._change_reason = 'User add new connections or concepts'
                        structure_changed = True
                    
                if structure_changed:
                    structure_rubric.save()
                
            if topological_json:
                topological = json.loads(topological_json)
                topological_rubric = evaluation.topological_rubric
                
                # Verificar se houve mudanças reais
                topological_changed = False
                
                branching_points = topological.get('branching_points')
                long_text_nodes = topological.get('long_text_nodes')
                edges_without_link_word = topological.get('edges_without_link_word')
                hierarchical_depth = topological.get('hierarchical_depth')
                cross_links = topological.get('cross_links')
                topological_score_normalized = topological.get('topological_score_normalized')
                
                if branching_points != topological_rubric.branching_points:
                    topological_rubric.branching_points = branching_points
                    topological_changed = True
                
                if long_text_nodes != topological_rubric.long_text_nodes:
                    topological_rubric.long_text_nodes = long_text_nodes
                    topological_changed = True
                
                if edges_without_link_word != topological_rubric.edges_without_link_word:
                    topological_rubric.edges_without_link_word = edges_without_link_word
                    topological_changed = True
                
                if hierarchical_depth != topological_rubric.hierarchical_depth:
                    topological_rubric.hierarchical_depth = hierarchical_depth
                    topological_changed = True
                
                if cross_links != topological_rubric.cross_links:
                    topological_rubric.cross_links = cross_links
                    topological_changed = True
                
                if topological_score_normalized != topological_rubric.topological_score_normalized:
                    topological_rubric.topological_score_normalized = topological_score_normalized
                    topological_changed = True
                
                # Só salvar se houve mudanças
                if topological_changed:
                    topological_rubric.modified = True
                    topological_rubric._change_reason = 'User modified topological values'
                    topological_rubric.save()
                    change_reason_parts.append('topological')
            
            if proposition_json:
                propositions = json.loads(proposition_json)
                proposition_rubric = evaluation.proposition_rubric
                
                propositions_classification = []
                propositions_correction = []
                for prop in propositions:
                    propositions_classification.append([prop['proposition'], prop.get('topics', []), [1.0] * len(prop['topics'])])
                    propositions_correction.append([prop['proposition'], prop['classification'], prop['justification']])
                
                # Verificar se houve mudanças reais
                proposition_changed = False
                
                if propositions_correction != proposition_rubric.propositions_correction:
                    proposition_rubric.propositions_correction = propositions_correction
                    proposition_changed = True
                
                if propositions_classification != proposition_rubric.propositions_classification:
                    proposition_rubric.propositions_classification = propositions_classification
                    proposition_changed = True
                
                # Só salvar se houve mudanças
                if proposition_changed:
                    proposition_rubric.modified = True
                    proposition_rubric._change_reason = 'User modified proposition classifications and corrections'
                    proposition_rubric.save()
                    proposition_rubric.calculate_correctness()
                    proposition_rubric.calculate_completeness()
                    change_reason_parts.append('propositions')

            if proposition_scores_json and evaluation.proposition_rubric:
                proposition_scores = json.loads(proposition_scores_json)
                proposition_rubric = evaluation.proposition_rubric

                # Permitir ajuste manual dos scores, priorizando o valor informado pelo usuário.
                manual_completeness = proposition_scores.get('completeness_score')
                manual_correctness = proposition_scores.get('correctness_score')
                manual_total = proposition_scores.get('proposition_score')

                proposition_score_changed = False

                if manual_completeness is not None:
                    manual_completeness = float(manual_completeness)
                    if proposition_rubric.completeness_score != manual_completeness:
                        proposition_rubric.completeness_score = manual_completeness
                        proposition_score_changed = True

                if manual_correctness is not None:
                    manual_correctness = float(manual_correctness)
                    if proposition_rubric.correctness_score != manual_correctness:
                        proposition_rubric.correctness_score = manual_correctness
                        proposition_score_changed = True

                if manual_total is not None:
                    manual_total = float(manual_total)
                    if proposition_rubric.proposition_score != manual_total:
                        proposition_rubric.proposition_score = manual_total
                        proposition_score_changed = True

                if proposition_score_changed:
                    proposition_rubric.modified = True
                    proposition_rubric._change_reason = 'User manually adjusted proposition scores'
                    proposition_rubric.save()
                    if 'proposition_scores' not in change_reason_parts:
                        change_reason_parts.append('proposition_scores')
                
            if infos_save_json:
                infos_save = json.loads(infos_save_json)
                infos_changed = False
                infos_details = []
                
                if 'comments_proposition' in infos_save and infos_save['comments_proposition'] != evaluation.comments_proposition:
                    evaluation.comments_proposition = infos_save['comments_proposition']
                    infos_changed = True
                    infos_details.append('comments_proposition')
                
                if 'comments_topological_scoring' in infos_save and infos_save['comments_topological_scoring'] != evaluation.comments_topological_scoring:
                    evaluation.comments_topological_scoring = infos_save['comments_topological_scoring']
                    infos_changed = True
                    infos_details.append('comments_topological')
                
                if 'comments_structure_classification' in infos_save and infos_save['comments_structure_classification'] != evaluation.comments_structure_classification:
                    evaluation.comments_structure_classification = infos_save['comments_structure_classification']
                    infos_changed = True
                    infos_details.append('comments_structure')

                if 'comments_visual_aspects' in infos_save and infos_save['comments_visual_aspects'] != evaluation.comments_visual_aspects:
                    evaluation.comments_visual_aspects = infos_save['comments_visual_aspects']
                    infos_changed = True
                    infos_details.append('comments_visual_aspects')
                
                if 'student_name' in infos_save and infos_save['student_name'] != evaluation.student_name:
                    evaluation.student_name = infos_save['student_name']
                    infos_changed = True
                    infos_details.append('student_name')
                
                if 'final_score' in infos_save and float(infos_save['final_score']) != evaluation.final_score:
                    evaluation.final_score = float(infos_save['final_score'])
                    infos_changed = True
                    infos_details.append('final_score')
                    
                if 'student_other_disabilities' in infos_save and infos_save['student_other_disabilities'] != evaluation.student_other_disabilities:
                    evaluation.student_other_disabilities = infos_save['student_other_disabilities']
                    infos_changed = True
                    infos_details.append('student_other_disabilities')
                
                if 'student_suspected_dyslexia' in infos_save:
                    student_suspected_dyslexia_value = True if infos_save['student_suspected_dyslexia'] == 'on' else False
                    if student_suspected_dyslexia_value != evaluation.student_suspected_dyslexia:
                        evaluation.student_suspected_dyslexia = student_suspected_dyslexia_value
                        infos_changed = True
                        infos_details.append('student_suspected_dyslexia')
                
                if infos_changed:
                    evaluation.modified = True
                    change_reason_parts.append(f"info ({', '.join(infos_details)})")
            
            # Só salvar evaluation se houve mudanças
            if change_reason_parts:
                evaluation._change_reason = f"User modified: {', '.join(change_reason_parts)}"
                evaluation.save()
            
            return JsonResponse({'status': 'success'})
         
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def generate_comments_structure_colors(request):
    """Salva as cores de classificação estrutural alteradas pelo usuário"""
    if request.method == 'POST':
        try:
            node_colors_json = request.POST.get('node_colors')
            evaluation_id = request.POST.get('evaluation_id')
            evaluation = get_object_or_404(CMEvaluationResult, user=request.user, id=evaluation_id)
                    
            return JsonResponse({
                'status': 'success',
                'comments': evaluation.generate_structure_classification_comments_from_values(json.loads(node_colors_json),
                                                                                              evaluation.concept_map.nodes_data)
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def save_proposition_changes(request):
    """Salva todas as alterações das proposições (classificação, justificação e tópicos)"""
    if request.method == 'POST':
        try:
            propositions_data_json = request.POST.get('propositions_data')
            evaluation_id = request.POST.get('evaluation_id')
            concept_map_id = request.POST.get('concept_map_id')
            user = request.user
                        
            evaluation = get_object_or_404(CMEvaluationResult, id=evaluation_id, concept_map__id=concept_map_id, user=user)
            
            if propositions_data_json:
                propositions_data = json.loads(propositions_data_json)
                
                proposition_rubric = evaluation.proposition_rubric
                
                propositions_classification = []
                propositions_correction = []
                for prop in propositions_data:
                    propositions_classification.append([prop['proposition'], prop.get('topics', []), [1.0] * len(prop['topics'])])
                    propositions_correction.append([prop['proposition'], prop['classification'], prop['justification']])
                
                
                proposition_specification, correctness_score = proposition_rubric.calculate_correctness_from_values(propositions_classification,
                                                                                                                    propositions_correction)
                
                completeness_specification, completeness_score = proposition_rubric.calculate_completeness_from_values(
                                                                                            evaluation.rubric.reference_document.propositions_weight,
                                                                                            propositions_classification,
                                                                                            evaluation.rubric.reference_document.propositions)

                comments = evaluation.generate_proposition_comments_from_values(completeness_specification, proposition_specification)

                return JsonResponse({
                    'status': 'success',
                    'correctness_score': correctness_score,
                    'completeness_score': completeness_score,
                    'total_score': round((correctness_score + completeness_score) / 2, 2),
                    'missing_topics': completeness_specification.get('topics_not_present', []),
                    'covered_topics': completeness_specification.get('topics_present', []),
                    'comments': comments
                })
            else:
                return JsonResponse({'status': 'error', 'message': 'Proposition rubric not found'}, status=404)
         
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def save_topological_values(request):
    if request.method == 'POST':
        try:
            topological_data_json = request.POST.get('topological_data')
            evaluation_id = request.POST.get('evaluation_id')
            concept_map_id = request.POST.get('concept_map_id')
            
            if topological_data_json:
                topological_data = json.loads(topological_data_json)
                
                evaluation = get_object_or_404(CMEvaluationResult, id=evaluation_id, 
                                              concept_map__id=concept_map_id, user=request.user)
                concept_map = evaluation.concept_map
                
                total_edges = concept_map.total_edges
                total_nodes = concept_map.total_nodes
                long_text_nodes = topological_data.get('long_text_nodes', 0)
                edges_without_link_word = topological_data.get('edges_without_link_word', 0)
                branching_points = topological_data.get('branching_points', 0)
                hierarchical_depth = topological_data.get('hierarchical_depth', 0)
                cross_links = topological_data.get('cross_links', 0)
                
                topological_score = evaluation.topological_rubric.calculate_topological_score_from_values(
                    long_text_nodes, edges_without_link_word,
                    branching_points,
                    hierarchical_depth,
                    cross_links,
                    total_nodes, total_edges
                )
                
                comments = evaluation.generate_topological_scoring_comments_from_values(
                    topological_score, evaluation.rubric.topological_level_configuration,
                    long_text_nodes, edges_without_link_word,
                    branching_points, hierarchical_depth, cross_links, 
                    evaluation.topological_rubric.topological_score_description)
                
                return JsonResponse({
                    'status': 'success',
                    'new_level': topological_score,
                    'new_normalized_score': round((topological_score / 6.0) * 10, 2),
                    'topological_general_comments': comments
                })
                
            else:
                return JsonResponse({'status': 'error', 'message': 'Missing data'}, status=400)
                
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def evaluation_loading(request):
    """Tela de carregamento antes da avaliação"""
    # Verificar se está processando extração de tópicos
    processing_topics = request.session.get('processing_topics', False)
    
    context = {
        'processing_topics': processing_topics
    }
    
    return render(request, 'loading.html', context)

@login_required
def check_evaluation_status(request):
    """Verificar status de avaliação via AJAX"""
    logger = logging.getLogger(__name__)
    
    if request.method == 'GET':
        evaluation_id = request.GET.get('evaluation_id')
        
        if not evaluation_id:
            return JsonResponse({'status': 'error', 'message': 'Missing evaluation_id'}, status=400)
        
        try:
            evaluation = CMEvaluationResult.objects.get(id=evaluation_id, user=request.user)
            logger.info(f"📊 Status check for evaluation {evaluation_id}: {evaluation.evaluation_status} (score: {evaluation.final_score})")
            
            return JsonResponse({
                'status': 'success',
                'evaluation_status': evaluation.evaluation_status,
                'final_score': float(evaluation.final_score) if evaluation.evaluation_status == 'completed' else None
            })
        except CMEvaluationResult.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Evaluation not found'}, status=404)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def process_evaluation_background(request):
    """Processar avaliação em background"""
    if request.method == 'POST':
        evaluation_id = request.POST.get('evaluation_id')
        
        if not evaluation_id:
            return JsonResponse({'status': 'error', 'message': 'Missing evaluation_id'}, status=400)
        
        try:
            evaluation = CMEvaluationResult.objects.get(id=evaluation_id, user=request.user)
            user = request.user
            rubric_id = evaluation.rubric.id
            concept_map_id = evaluation.concept_map.id
            
            logger = logging.getLogger(__name__)
            
            # Processar em thread separada
            def process_evaluation():
                try:
                    logger.info(f"🚀 Starting background processing for evaluation {evaluation_id}")
                    # Usar a função auxiliar diretamente (sem simular request)
                    result = _process_evaluation_logic(user, rubric_id, concept_map_id)
                    logger.info(f"✅ Background processing completed for evaluation {evaluation_id}")
                    logger.info(f"   Final status: {result.evaluation_status}")
                    logger.info(f"   Final score: {result.final_score}")
                    
                except Exception as e:
                    logger.error(f"❌ Error in background processing: {str(e)}")
                    traceback.print_exc()
                    
                    # Marcar como falha
                    try:
                        evaluation_failed = CMEvaluationResult.objects.get(id=evaluation_id, user=user)
                        evaluation_failed.evaluation_status = 'failed'
                        evaluation_failed._change_reason = f'Processing failed: {str(e)}'
                        evaluation_failed.save()
                        logger.error(f"Evaluation {evaluation_id} marked as failed")
                    except Exception as e2:
                        logger.error(f"Failed to mark evaluation as failed: {str(e2)}")
            
            # Iniciar thread
            thread = threading.Thread(target=process_evaluation)
            thread.daemon = True
            thread.start()
            
            return JsonResponse({
                'status': 'success', 
                'message': 'Evaluation processing started',
                'evaluation_id': evaluation.id
            })
                
        except CMEvaluationResult.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Evaluation not found'}, status=404)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def process_topics_extraction(request):
    """Processar extração de tópicos do reference document"""
    if request.method == 'POST':
        try:
            reference_document_id = request.session.get('reference_document_id')
            rubric_id = request.session.get('rubric_id')
            
            if not reference_document_id or not rubric_id:
                return JsonResponse({'status': 'error', 'message': 'Missing reference document or rubric ID'}, status=400)
            
            reference_document = ReferenceDocument.objects.get(id=reference_document_id, user=request.user)
            rubric_configuration = RubricConfiguration.objects.get(id=rubric_id, user=request.user)
            
            # Processar extração de tópicos
            nodes, edges = extract_essential_topics_from_reference_document(reference_document.full_text)
            reference_document.nodes_data = nodes
            reference_document.edges_data = edges
            reference_document._change_reason = 'Extracted essential topics and relations from reference document by LLM, created node and edge data'
            reference_document.save()
            reference_document.create_propositions()
            
            # Associar reference_document ao rubric_configuration
            rubric_configuration.reference_document = reference_document
            rubric_configuration._change_reason = 'Associated reference document after topics extraction'
            rubric_configuration.save()
            
            # Limpar flag de processamento
            request.session['processing_topics'] = False
            
            return JsonResponse({'status': 'success', 'message': 'Topics extracted successfully'})
            
        except ReferenceDocument.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Reference document not found'}, status=404)
        except RubricConfiguration.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Rubric configuration not found'}, status=404)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def evaluation_history(request):
    """Display all concept map evaluations for the user with optimized queries"""
    # Buscar todas as avaliações com related objects em uma query
    evaluations = CMEvaluationResult.objects.filter(
        user=request.user
    ).order_by('-created_at')
    
    # Criar mapeamento concept_map_id -> evaluation
    eval_map = {e.concept_map_id: e for e in evaluations}
    
    # Buscar todos os concept maps do usuário
    concept_maps = ConceptMap.objects.filter(user=request.user).order_by('-created_at')
    
    # Construir dados de avaliação
    evaluations_data = []
    unique_rubrics_map = {}
    for cm in concept_maps:
        evaluation = eval_map.get(cm.id)
        if evaluation:
            if evaluation.rubric and evaluation.rubric.id not in unique_rubrics_map:
                unique_rubrics_map[evaluation.rubric.id] = evaluation.rubric

            evaluations_data.append({
                'concept_map': cm,
                'rubric': evaluation.rubric if evaluation else None,
                'reference_document': evaluation.rubric.reference_document if evaluation.rubric else None,
                'status': evaluation.evaluation_status,
                'evaluation': evaluation,
                'final_score': evaluation.final_score if evaluation else None,
            })
    
    context = {
        'evaluations_data': evaluations_data,
        'unique_rubrics': list(unique_rubrics_map.values()),
    }
    
    return render(request, 'evaluation_history.html', context)
import json
from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from collections import defaultdict, deque
from openai import OpenAI
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from simple_history.models import HistoricalRecords
from .utils import topological_level_descriptions

# Abstract Base Classes
class SoftDeleteModel(models.Model):
    """Abstract model for soft delete functionality"""
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        abstract = True
    
    def soft_delete(self):
        """Marca o objeto como deletado ao invés de deletar fisicamente"""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def delete(self, using=None, keep_parents=False):
        """Soft delete by default for model instances."""
        self.soft_delete()
    
    def restore(self):
        """Restaura um objeto que foi soft deleted"""
        self.is_deleted = False
        self.deleted_at = None
        self.save()

class ActiveManager(models.Manager):
    """Manager que retorna apenas objetos não deletados"""
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)

class AllObjectsManager(models.Manager):
    """Manager que retorna todos os objetos incluindo deletados"""
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that soft-deletes rows instead of removing them."""
    def delete(self):
        return super().update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()

class TimeStampedModel(models.Model):
    """Abstract model with automatic timestamp tracking"""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True

class UserOwnedModel(TimeStampedModel, SoftDeleteModel):
    """Abstract model for user-owned objects with modification tracking and soft delete"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='%(class)s_set')
    modified = models.BooleanField(default=False)
    
    class Meta:
        abstract = True

class ConceptMap(UserOwnedModel):
    filename = models.CharField(max_length=255, db_index=True)
    nodes_data = models.JSONField()  # Armazena o dicionário de nodes
    edges_data = models.JSONField()  # Armazena a lista de edges
    node_styles_data = models.JSONField(null=True, blank=True)  # Metadados visuais dos nos
    edge_styles_data = models.JSONField(null=True, blank=True)  # Metadados visuais das arestas
    propositions = models.JSONField(null=True, blank=True)  # Armazena a lista de propositions
    concept_map_image_data = models.TextField(null=True, blank=True)  # Armazena a imagem do mapa conceitual como base64
    
    total_nodes = models.IntegerField(default=0)
    total_edges = models.IntegerField(default=0)
    
    # Managers
    objects = ActiveManager()  # Manager padrão - apenas objetos não deletados
    all_objects = AllObjectsManager()  # Para acessar todos incluindo deletados
    
    # Histórico
    history = HistoricalRecords(excluded_fields=['updated_at'])
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['filename']),
        ]
    
    @classmethod
    def latest_for_user(cls, user):
        """Get latest concept map for user"""
        return cls.objects.filter(user=user).latest('updated_at')
    
    def __str__(self):
        return f"{self.user.username} - {self.filename} ({self.created_at})"
    
    def create_propositions(self):
        self.total_nodes = len(self.nodes_data)
        self.total_edges = len(self.edges_data)
        
        propositions = []
        for edge in self.edges_data:
            frase = f"{self.nodes_data[edge[0]]} {edge[2]} {self.nodes_data[edge[1]]}"
            propositions.append(frase)
            
        self.propositions = propositions
        self.save()
    
class ReferenceDocument(UserOwnedModel):
    filename = models.CharField(max_length=255, db_index=True)
    full_text = models.TextField(null=False)
    nodes_data = models.JSONField(null=True, blank=True)
    edges_data = models.JSONField(null=True, blank=True)
    propositions = models.JSONField(null=True, blank=True)
    propositions_weight = models.JSONField(null=True, blank=True)
    
    # Managers
    objects = ActiveManager()
    all_objects = AllObjectsManager()
    
    # Histórico
    history = HistoricalRecords(excluded_fields=['updated_at', 'full_text'])

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['filename']),
        ]
    
    @classmethod
    def latest_for_user(cls, user):
        """Get latest reference document for user"""
        return cls.objects.filter(user=user).latest('updated_at')
    
    def __str__(self):
        return f"{self.user.username} - {self.filename}"
    
    def create_propositions(self):
        propositions = []
        for edge in self.edges_data:
            frase = f"{edge[0]} {edge[2]} {edge[1]}"
            propositions.append(frase)
        self.propositions = propositions
        self.save()

class PropositionRubric(UserOwnedModel):
    evaluation_result = models.ForeignKey('CMEvaluationResult', on_delete=models.CASCADE, related_name='proposition_rubrics', null=False, blank=False)
    concept_map = models.ForeignKey('ConceptMap', on_delete=models.CASCADE, related_name='proposition_rubrics')
    propositions_classification = models.JSONField(null=True, blank=True)
    propositions_correction = models.JSONField(null=True, blank=True)
    
    completeness_score = models.FloatField(default=0.0)
    correctness_score = models.FloatField(default=0.0)
    
    completeness_specification = models.JSONField(null=True, blank=True)
    proposition_specification = models.JSONField(null=True, blank=True)
    proposition_score = models.FloatField(default=0.0)
    
    # Managers
    objects = ActiveManager()
    all_objects = AllObjectsManager()
    
    # Histórico
    history = HistoricalRecords(excluded_fields=['updated_at'])
    
    def __str__(self):
        return f"Proposition Rubric - {self.concept_map.filename} (User: {self.user.username})"
    
    def calculate_completeness_from_values(self, topics_weight,propositions_classification,
                                           topics_ref_doc):
        topics_present = {}
        sum_total_topics_present = sum(list(topics_weight.values()))
        for classification in propositions_classification:
            for topic, similarity_score in zip(classification[1], classification[2]):
                if similarity_score >= 0.7 and topic in topics_ref_doc:
                    proposition_weight = topics_weight.get(topic, 1)
                    if topic not in topics_present:
                        topics_present[topic] = proposition_weight
                else:
                    continue
        
        topics_not_present = set(topics_ref_doc) - set(topics_present.keys())
        completeness_specification = {"topics_present": topics_present, 
                                        "topics_not_present": list(topics_not_present)}
        # Normalizar score para [0, 1] para garantir nota máxima de 10
        score = sum(topics_present.values()) / sum_total_topics_present
        score = min(score, 1.0)  # Garantir que não ultrapasse 100%
        completeness_score = round(score * 10, 2)
        return completeness_specification, completeness_score
    
    def calculate_completeness(self):
        topics_weight = self.evaluation_result.rubric.reference_document.propositions_weight
        topics_ref_doc = self.evaluation_result.rubric.reference_document.propositions
        
        self.completeness_specification, self.completeness_score = \
                                self.calculate_completeness_from_values(topics_weight,
                                                                        self.propositions_classification,
                                                                        topics_ref_doc)
        self.save()
    
    def calculate_correctness_from_values(self, propositions_classification, propositions_correction):
        correction_count = 0
        proposition_specification = {}
        
        for classification, related_topics in zip(propositions_correction, propositions_classification):
            correctness = str(classification[1]).lower()
            proposition_specification[classification[0]] = {"related_topic": related_topics[1],
                                                            "justification": classification[2]}
            if correctness in ['incorrect', 'incorreto']:
                correction_count += 0
                proposition_specification[classification[0]]["classification"] = "incorrect"
            elif correctness in ['partially correct', 'parcialmente correto']:
                correction_count += 0.5
                proposition_specification[classification[0]]["classification"] = "partially correct"
            elif correctness in ['correct', 'correto']:
                correction_count += 1
                proposition_specification[classification[0]]["classification"] = "correct"
        
        total_score = len(proposition_specification.keys())
        # Normalizar score para [0, 1] para garantir nota máxima de 10
        score = correction_count / total_score if total_score > 0 else 0
        score = min(score, 1.0)  # Garantir que não ultrapasse 100%
        correctness_score = round(score * 10, 2)
        return proposition_specification, correctness_score
    
    def calculate_correctness(self):
        self.proposition_specification, self.correctness_score = self.calculate_correctness_from_values(self.propositions_classification,
                                                                                                        self.propositions_correction)
        self.save()
    
    def calculate_proposition_score(self):
        self.calculate_completeness()
        self.calculate_correctness()
        
        proposition_score = (self.completeness_score + self.correctness_score) / 2
        self.proposition_score = round(proposition_score, 2)
        self.save()
    
    
class TopologicalScoringRubric(UserOwnedModel):
    evaluation_result = models.ForeignKey('CMEvaluationResult', on_delete=models.CASCADE, related_name='topological_scoring_rubrics', null=False, blank=False)
    concept_map = models.ForeignKey('ConceptMap', on_delete=models.CASCADE, related_name='topological_scoring_rubrics', null=True, blank=True)
    
    long_text_nodes = models.IntegerField(default=0)
    long_text_nodes_description = models.TextField(default="")
    edges_without_link_word = models.IntegerField(default=0)
    edges_without_link_word_description = models.TextField(default="")
    branching_points = models.IntegerField(default=0)
    hierarchical_depth = models.IntegerField(default=0)
    cross_links = models.IntegerField(default=0)
    cross_links_description = models.TextField(default="")
    
    topological_score = models.IntegerField(default=0)
    topological_score_normalized = models.FloatField(default=0.0)
    topological_score_description = models.JSONField(null=True, blank=True)
    
    # Managers
    objects = ActiveManager()
    all_objects = AllObjectsManager()
    
    # Histórico
    history = HistoricalRecords(excluded_fields=['updated_at'])
    
    def __str__(self):
        return f"Topological Scoring for {self.concept_map.filename} - Level {self.topological_score}"
    
    def save(self, *args, **kwargs):
        """Automatically calculate topological metrics when object is created"""
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        # Calcular métricas apenas na criação do objeto
        if is_new and self.concept_map:
            self.count_long_text_nodes()
            self.missing_linking_words()
            self.count_branching_points()
            self.hierarchy_depth()
            self.count_cross_links()
            self.calculate_topological_score()
    
    def count_long_text_nodes(self):
        long_text_count = 0
        long_text_nodes_descriptions = []
        for label in self.concept_map.nodes_data.values():
            if len(label.split()) > 10:
                long_text_count += 1
                long_text_nodes_descriptions.append(label)

        self.long_text_nodes = long_text_count
        self.long_text_nodes_description = ", ".join(long_text_nodes_descriptions)
        self.save()

    def missing_linking_words(self):
        missing = 0
        edges_without_link_word_description = []
        for n1, n2, link in self.concept_map.edges_data:
            if link is None or link.strip() == "" or link.strip() == "→":
                missing += 1
                edges_without_link_word_description.append(f"{self.concept_map.nodes_data[n1]} -> {self.concept_map.nodes_data[n2]}")
        self.edges_without_link_word = missing
        self.edges_without_link_word_description = ", ".join(edges_without_link_word_description)
        self.save()

    def build_graph(self):
        graph = defaultdict(list)
        reverse_graph = defaultdict(list)

        for src, dst, _ in self.concept_map.edges_data:
            graph[src].append(dst)
            reverse_graph[dst].append(src)

        return graph, reverse_graph

    def count_branching_points(self):
        outgoing = defaultdict(int)
        for src, _, _ in self.concept_map.edges_data:
            outgoing[src] += 1

        self.branching_points = sum(1 for v in outgoing.values() if v >= 2)
        self.save()

    def find_root_nodes(self):
        has_incoming = set(dst for _, dst, _ in self.concept_map.edges_data)
        return [n for n in self.concept_map.nodes_data if n not in has_incoming]

    def hierarchy_depth(self):
        graph, _ = self.build_graph()
        roots = self.find_root_nodes()

        if not roots:
            return 0

        max_depth = 0

        for root in roots:
            visited = set()
            queue = deque([(root, 0)])

            while queue:
                node, depth = queue.popleft()
                visited.add(node)
                max_depth = max(max_depth, depth)

                for neighbor in graph[node]:
                    if neighbor not in visited:
                        queue.append((neighbor, depth + 1))

        self.hierarchical_depth = max_depth
        self.save()

    def count_cross_links(self):
        graph, _ = self.build_graph()
        roots = set(self.find_root_nodes())

        def has_path(start, end, visited):
            if start == end:
                return True
            visited.add(start)
            for n in graph[start]:
                if n not in visited:
                    if has_path(n, end, visited):
                        return True
            return False

        cross_links = 0
        cross_links_descriptions = []
        for src, dst, _ in self.concept_map.edges_data:
            if src not in roots and dst not in roots:
                # remove aresta temporariamente
                graph[src].remove(dst)
                if has_path(dst, src, set()):
                    cross_links += 1
                    cross_links_descriptions.append(f"{self.concept_map.nodes_data[src]} -> {self.concept_map.nodes_data[dst]}")
                graph[src].append(dst)

        self.cross_links = cross_links
        self.cross_links_description = ", ".join(cross_links_descriptions)
        self.save()
    
    def calculate_topological_score_from_values(self, long_text_nodes, edges_without_link_word, 
                                           branching_points, hierarchical_depth, cross_links,
                                           total_nodes, total_edges):
        """
        Calcula o score topológico baseado em valores fornecidos.
        Método auxiliar que permite calcular o score com valores customizados.
        """
        concept_long_text_percent = round((long_text_nodes / total_nodes), 1) if total_nodes > 0 else 0
        linking_phrase_percent = round((edges_without_link_word / total_edges), 1) if total_edges > 0 else 0
        
        topological_score = 0
        for level in range(6, -1, -1):
            configs = self.evaluation_result.rubric.topological_level_configuration[str(level)]
            if  concept_long_text_percent <= float(configs['concept_long_text']) and \
                linking_phrase_percent <= float(configs['linking_phrase']) and \
                branching_points >= int(configs['branching']) and \
                hierarchical_depth >= int(configs['hierarchy']) and \
                cross_links >= int(configs['crosslinks']):            
                
                topological_score = level
                break
        
        return topological_score

    def calculate_topological_score(self):
        """Calcula o score topológico usando os valores atuais do objeto"""
        total_nodes = len(self.concept_map.nodes_data)
        total_edges = len(self.concept_map.edges_data)
        
        topological_score = self.calculate_topological_score_from_values(
            self.long_text_nodes,
            self.edges_without_link_word,
            self.branching_points,
            self.hierarchical_depth,
            self.cross_links,
            total_nodes,
            total_edges
        )
        
        self.topological_score = topological_score
        score = topological_score / 6.0 if topological_score > 0 else 0.0
        self.topological_score_normalized = round(score * 10, 2)
        self.save()
    
class StructureClassificationRubric(UserOwnedModel):
    evaluation_result = models.ForeignKey('CMEvaluationResult', on_delete=models.CASCADE, related_name='structure_classification_rubrics', null=False, blank=False)
    concept_map = models.ForeignKey('ConceptMap', on_delete=models.CASCADE, related_name='structure_classification_rubrics')
    
    node_colors = models.JSONField(null=True, blank=True)  # Armazena as cores dos nós
    size_spokes_threshold = models.IntegerField(default=3, validators=[MinValueValidator(1)])
    size_chain_threshold = models.IntegerField(default=3, validators=[MinValueValidator(1)])
    
    concept_map_image = models.TextField(null=True, blank=True)  # Armazena a imagem do mapa conceitual como base64
    description = models.JSONField(null=True, blank=True)
    
    # Managers
    objects = ActiveManager()
    all_objects = AllObjectsManager()
    
    # Histórico
    history = HistoricalRecords(excluded_fields=['updated_at', 'concept_map_image']) 

    def __str__(self):
        return f"Structure Classification - {self.concept_map.filename} (User: {self.user.username})"
    
    def color_map(self):
        nodes = self.concept_map.nodes_data
        edges = self.concept_map.edges_data
        
        # Construir grafos de entrada e saída
        outgoing = defaultdict(list)  # node_id -> [nodes de saída]
        incoming = defaultdict(list)  # node_id -> [nodes de entrada]
        
        for edge in edges:
            source, target = edge[0], edge[1]
            outgoing[source].append(target)
            incoming[target].append(source)
        
        # Inicializar todas as cores como branco
        node_colors = {node_id: "white" for node_id in nodes.keys()}
        
        # 1. IDENTIFICAR CHAINS (nós azuis)
        # Um nó faz parte de uma chain se tem exatamente 1 entrada e 1 saída
        # E faz parte de uma sequência de pelo menos 4 nós
        
        def encontrar_chains():
            chains = []
            visitados = set()
            
            for node_id in nodes.keys():
                if node_id in visitados:
                    continue
                    
                # Verificar se o nó pode ser parte de uma chain
                if len(incoming[node_id]) == 1 and len(outgoing[node_id]) == 1:
                    # Tentar construir a chain completa
                    chain = []
                    current = node_id
                    
                    # Voltar até o início da chain
                    while len(incoming[current]) == 1 and current not in chain:
                        prev = incoming[current][0]
                        if len(outgoing[prev]) == 1:
                            chain.insert(0, prev)
                            current = prev
                        else:
                            break
                    
                    # Adicionar o nó atual
                    if current not in chain:
                        chain.append(current)
                    
                    # Avançar até o fim da chain
                    current = node_id
                    while len(outgoing[current]) == 1 and current not in chain[1:]:
                        next_node = outgoing[current][0]
                        if len(incoming[next_node]) == 1:
                            chain.append(next_node)
                            current = next_node
                        else:
                            break
                    
                    # Se a chain tem pelo menos 3 nós, adicionar
                    if len(chain) >= self.size_chain_threshold:
                        chains.append(chain)
                        visitados.update(chain)
            
            return chains
        
        chains = encontrar_chains()
        
        # Pintar nós das chains de azul
        for chain in chains:
            for node_id in chain:
                node_colors[node_id] = "blue"
        
        # 2. IDENTIFICAR SPOKES (nós laranjas)
        # Um nó é spoke se tem mais de 4 nós folha conectados
        # Nó folha = nó sem saídas (grau de saída = 0)
        
        def contar_folhas_conectadas(node_id):
            """Conta quantos nós folha estão diretamente conectados a este nó"""
            folhas = 0
            nos_folhas = []
            for child in outgoing[node_id]:
                if len(outgoing[child]) == 0:  # É folha
                    folhas += 1
                    nos_folhas.append(child)
            return folhas, nos_folhas

        # Pintar nós spoke de laranja
        for node_id in nodes.keys():
            if node_colors[node_id] == "white":  # Só pintar se ainda não foi pintado
                folhas, nos_folhas = contar_folhas_conectadas(node_id)
                if folhas > self.size_spokes_threshold:
                    node_colors[node_id] = "orange"
                    for folha_id in nos_folhas:
                        node_colors[folha_id] = "orange"
        
        self.node_colors = node_colors
        self.save()
    
class RubricConfiguration(TimeStampedModel, SoftDeleteModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, help_text="The user who created this configuration")
    name = models.CharField(max_length=100)
    reference_document = models.ForeignKey(ReferenceDocument, on_delete=models.CASCADE,
                                          help_text="The reference document associated with this configuration",
                                          null=True, blank=True)
    
    # Rubrics status
    proposition_rubric_checkbox = models.BooleanField(default=False)
    topological_scoring_rubric_checkbox = models.BooleanField(default=False)
    half_correct = models.BooleanField(default=True)
    structure_classification = models.BooleanField(default=True)
    visual_aspects = models.BooleanField(default=False)
    
    # Weights (must be positive)
    proposition_weight = models.IntegerField(default=1, validators=[MinValueValidator(0)])
    topological_scoring_weight = models.IntegerField(default=1, validators=[MinValueValidator(0)])
    topological_level_configuration = models.JSONField(null=True, blank=True)
    
    # Managers
    objects = ActiveManager()
    all_objects = AllObjectsManager()
    
    # Histórico
    history = HistoricalRecords(excluded_fields=['updated_at'])

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['name', 'user']),
        ]

    def __str__(self):
        return f"Rubric Configuration: {self.name}"
    
    @classmethod
    def latest_for_user(cls, user):
        """Get latest rubric configuration for user"""
        return cls.objects.filter(user=user).latest('updated_at')
    
    def clean(self):
        """Validate that at least one rubric is selected"""
        if not (self.proposition_rubric_checkbox or 
                self.topological_scoring_rubric_checkbox or 
                self.structure_classification or
                self.visual_aspects):
            raise ValidationError('At least one rubric must be selected')
    
    def soft_delete(self):
        """Soft delete da rubrica e de todas as avaliações relacionadas"""
        # Fazer soft delete de todas as avaliações relacionadas
        for evaluation in self.evaluations.all():
            evaluation.soft_delete()
        
        # Depois fazer soft delete da própria rubrica
        super().soft_delete()

class CMEvaluationResult(TimeStampedModel, SoftDeleteModel):
    concept_map = models.ForeignKey(ConceptMap, on_delete=models.CASCADE, related_name='evaluations')
    rubric = models.ForeignKey(RubricConfiguration, on_delete=models.CASCADE, related_name='evaluations')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    modified = models.BooleanField(default=False)
    
    # campos específicos para avaliação de dislexia
    student_suspected_dyslexia = models.BooleanField(default=False)
    student_other_disabilities = models.TextField(null=True, blank=True)
    
    # Status da avaliação
    EVALUATION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    evaluation_status = models.CharField(max_length=20, choices=EVALUATION_STATUS_CHOICES, default='pending')
    
    # Rubric results - each evaluation has its own rubric instances
    proposition_rubric = models.ForeignKey(PropositionRubric, on_delete=models.SET_NULL, null=True, blank=True, related_name='evaluation_results')
    topological_rubric = models.ForeignKey(TopologicalScoringRubric, on_delete=models.SET_NULL, null=True, blank=True, related_name='evaluation_results')
    structure_rubric = models.ForeignKey(StructureClassificationRubric, on_delete=models.SET_NULL, null=True, blank=True, related_name='evaluation_results')
    
    # Detailed results
    final_score = models.FloatField(default=0.0, validators=[MinValueValidator(0.0), MaxValueValidator(10.0)])
    comments_proposition = models.TextField(null=True, blank=True)
    comments_topological_scoring = models.TextField(null=True, blank=True)
    comments_structure_classification = models.TextField(null=True, blank=True)
    comments_visual_aspects = models.TextField(null=True, blank=True)
    student_name = models.CharField(max_length=255, null=True, blank=True)
    
    # Managers
    objects = ActiveManager()
    all_objects = AllObjectsManager()
    
    # Histórico
    history = HistoricalRecords(excluded_fields=['updated_at'])
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['concept_map', 'rubric']),
        ]
    
    def __str__(self):
        return f"Evaluation of {self.concept_map.filename} by {self.user.username}"
    
    def calculate_final_score(self):
        rubric_configs = [
            ('proposition_rubric_checkbox', 'proposition_rubric', 'proposition_weight', 'proposition_score'),
            ('topological_scoring_rubric_checkbox', 'topological_rubric', 'topological_scoring_weight', 'topological_score_normalized'),
        ]
        
        total_weighted_score = 0.0
        total_weight = 0
        
        for checkbox_field, rubric_field, weight_field, score_field in rubric_configs:
            if getattr(self.rubric, checkbox_field):
                rubric_obj = getattr(self, rubric_field)  # Buscar do evaluation, não da rubric
                # Verificar se o objeto da rubrica existe
                if rubric_obj is None:
                    continue
                score = getattr(rubric_obj, score_field)
                weight = getattr(self.rubric, weight_field)
                total_weighted_score += score * weight
                total_weight += weight
        
        self.final_score = round(total_weighted_score / total_weight if total_weight > 0 else 0.0, 2)
        if self.rubric.proposition_rubric_checkbox and self.proposition_rubric:
            self.generate_proposition_comments()
        if self.rubric.structure_classification and self.structure_rubric:
            self.generate_structure_classification_comments()
        if self.rubric.topological_scoring_rubric_checkbox and self.topological_rubric:
            self.generate_topological_scoring_comments()
            
        self.save()
    
    def generate_proposition_comments_from_values(self, completeness_specification, proposition_specification):
        comment = ""
        
        comment_lacking_topics = str(_('The following topics were not present in your concept map, and could be further studied:')) + "\n"
        for topic_not_present in completeness_specification['topics_not_present']:
            comment_lacking_topics += f"- {topic_not_present}.\n"
        
        comment += comment_lacking_topics + "\n"
        
        comment_most_wrong_concepts = str(_('The following topics had some misunderstanding:')) + "\n"
        
        proposition_mistakes = set()
        for proposition, details in proposition_specification.items():
            if details['classification'] in ['incorrect', 'partially correct']:
                for prop in details['related_topic']:
                    proposition_mistakes.add(prop)
        
        for prop in proposition_mistakes:
            comment_most_wrong_concepts += f"- {prop}\n"        
        
        comment += comment_most_wrong_concepts + "\n"
        return comment
    
    def generate_proposition_comments(self):
        if not self.proposition_rubric:
            return
        
        comment = self.generate_proposition_comments_from_values(self.proposition_rubric.completeness_specification,
                                                                self.proposition_rubric.proposition_specification)
        self.comments_proposition = comment
        self.save()
        
    def generate_topological_scoring_comments_from_values(self, topological_score, topological_level_configuration,
                                                          long_text_nodes, edges_without_link_word, 
                                                        branching_points, hierarchical_depth, cross_links, topological_score_description):
        if not self.topological_rubric:
            return
        
        comment = str(_("Your concept map was classified as "))
        level_description, structural_meaning = topological_level_descriptions()
        comment += f"{level_description[str(topological_score)]}\n"
        comment += str(_(f"Meaning: "))
        comment += f"{structural_meaning[str(topological_score)]}\n\n"
        if topological_score != 6:
        #     comment += str(_('Your concept map has a well-defined structure with good use of linking phrases and appropriate concept lengths. Great job!')) + "\n"
        # else:
            comment += str(_('To improve your concept map structure, consider the following suggestions:')) + "\n"
            if long_text_nodes > 0:
                comment += str(_(' - Reduce the number of concepts with long text descriptions. Aim for concise and clear labels. The following concepts have too long descriptions:')) + "\n"
                comment += f"{self.topological_rubric.long_text_nodes_description}\n"
            if edges_without_link_word > 0:
                comment += str(_(' - Ensure all connections between concepts have linking phrases, this will show how the concepts are related. The following connections are missing linking phrases:')) + "\n"
                comment += f"{self.topological_rubric.edges_without_link_word_description}\n"
            if int(cross_links) < int(topological_level_configuration['6']['crosslinks']):
                comment += str(_(' - Add more cross-links in your concept map to enhance connectivity by connecting concepts in different branches of the concept map.')) + "\n"
            if int(hierarchical_depth) < int(topological_level_configuration['6']['hierarchy']):
                comment += str(_(' - Try to increase the depth of your concept map by adding more connections on the on different levels of hierarchy.')) + "\n"
            if int(branching_points) < int(topological_level_configuration['6']['branching']):
                comment += str(_(' - Add more branching points in your concept map to improve the organization and clarity of concepts.')) + "\n"
        
        return comment
    
    def generate_topological_scoring_comments(self):
        if not self.topological_rubric:
            return
        
        self.comments_topological_scoring = self.generate_topological_scoring_comments_from_values(self.topological_rubric.topological_score,
                                                               self.rubric.topological_level_configuration,
                                                               self.topological_rubric.long_text_nodes,
                                                               self.topological_rubric.edges_without_link_word,
                                                               self.topological_rubric.branching_points,
                                                               self.topological_rubric.hierarchical_depth,
                                                               self.topological_rubric.cross_links,
                                                               self.topological_rubric.topological_score_description)
        self.save()
    
    def generate_structure_classification_comments_from_values(self, structure_node_colors, concept_map_nodes):
        missing_connections = str(_("The connections and concepts in the green dotted lines are suggestions for improving your concept map.")) + "\n"
        
        orange_nodes = [node for node, color in structure_node_colors.items() if color == "orange"]
        blue_nodes = [node for node, color in structure_node_colors.items() if color == "blue"]
        white_nodes = [node for node, color in structure_node_colors.items() if color == "white"]
        if len(white_nodes) > 0:
            missing_connections += str(_('The white concepts are classified as network because they are well-connected across different levels of the concept map. Good job!')) + "\n"
        
        if len(orange_nodes) > 0:
            missing_connections += str(_('The following concepts are classified as spokes (orange) because they don\'t have sufficient connections between different levels of the concept map, try adding more connections to these concepts to improve your structure:')) + "\n"
            for item in orange_nodes:
                missing_connections += f"- {concept_map_nodes[item]}\n"
                
        if len(blue_nodes) > 0:
            missing_connections += str(_('The following concepts are classified as chains (blue) because they are connected in a linear way, so there is little to no connections between concepts on different hierarchy levels, try adding more connections to these concepts to improve your structure:')) + "\n"
            for item in blue_nodes:
                missing_connections += f"- {concept_map_nodes[item]}\n"
      
        return missing_connections
    
    def generate_structure_classification_comments(self):
        if not self.structure_rubric:
            return
        
        missing_connections = self.generate_structure_classification_comments_from_values(self.structure_rubric.node_colors,
                                                                                          self.concept_map.nodes_data)
        self.comments_structure_classification = missing_connections
        self.save()
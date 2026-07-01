import os
import json
from django.conf import settings
from openai import OpenAI
import pdfplumber
from django.utils.translation import gettext_lazy as _
try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    
def topological_level_descriptions():
    level_description = {"0": _('Structure Absent'),
                        "1": _('Initial Structure'),
                        "2": _('Basic Structure'),
                        "3": _('Emerging Structure'),
                        "4": _('Consolidated Structure'),
                        "5": _('Integrated Structure'),
                        "6": _('Advanced Structure')}
    
    structural_meaning =  {"0": _('The map is not yet a concept map; it is a block of text with scattered lines. There are no linking words, and the nodes contain long explanations. The concept map is abscent of the desired attribute (conceptual structure).'),
                            "1": _('The student begins to use short concepts, but most connections still lack linking words. The structure is still linear. The concept map is an initial approach, but far from ideal.'),
                            "2": _('Concepts now predominate over long texts. Linking words are present, with less than half missing. There is little branching. The concept map meets the minimum, but is inconsistent.'),
                            "3": _('The map is free of long texts. All linking words are present. Branching is noticeable (3–4 points), but hierarchical depth is still limited (< 3 levels). The concept map presents a functional structure, although not very deep.'),
                            "4": _('The map meets the criteria of level 3, but now with high branching (5–6 points) and developed hierarchy (≥ 3 levels). Still no cross-links. The concept map presents a solid structure, but without integration between branches.'),
                            "5": _('Everything level 4 offers, plus 1 to 2 cross-links. The student can now establish relationships between different parts of the map. The concept map presents a robust structure with evidence of relational thinking.'),
                            "6": _('Maximum level. It presents very high branching (≥ 7 points), developed hierarchy, and multiple cross-links (> 2). Indicates high capacity for conceptual integration and differentiation. The concept map presents a full mastery of the tool and knowledge structuring.')}
    
    return level_description, structural_meaning

def extract_proposition_from_map(map_path):
    """
    Extrai nós e arestas de um mapa conceitual em formato Excalidraw JSON.
    
    Se uma ponta de seta não tem binding e está próxima de uma forma geométrica,
    ela é ligada automaticamente.
    """
    nodes = {}
    edges = []
    
    try:
        with open(map_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return nodes, edges
    
    if not isinstance(data, dict):
        return nodes, edges
    
    elements = data.get('elements', [])
    if not elements:
        return nodes, edges

    # Funções auxiliares
    def shape_center(shape_el):
        """Calcula o centro de uma forma geométrica."""
        if not isinstance(shape_el, dict):
            return None
        x, y, w, h = shape_el.get('x'), shape_el.get('y'), shape_el.get('width'), shape_el.get('height')
        return (x + w, y + h) if None not in (x, y, w, h) else None

    def arrow_point_absolute(arrow_el, point_idx):
        """Converte um ponto relativo de seta para coordenadas absolutas."""
        points = arrow_el.get('points') or []
        if not points or abs(point_idx) >= len(points):
            return None
        point = points[point_idx]
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        ax, ay = arrow_el.get('x'), arrow_el.get('y')
        return (ax + point[0], ay + point[1]) if None not in (ax, ay) else None

    def nearest_shape(point, shapes_map, exclude_ids=None):
        """Encontra a forma geométrica mais próxima a um ponto (distância < 100px)."""
        if point is None:
            return None
        
        exclude_ids = set(exclude_ids or [])
        nearest, min_dist = None, 100.0  # threshold de 100px
        
        for shape_id, shape_el in shapes_map.items():
            if shape_id in exclude_ids:
                continue
            center = shape_center(shape_el)
            if center:
                dist = ((center[0] - point[0])**2 + (center[1] - point[1])**2)**0.5
                if dist < min_dist:
                    min_dist = dist
                    nearest = shape_id
        
        return nearest

    def is_point_inside_shape(text_el, shape_el):
        """Verifica se um elemento de texto está dentro de uma forma."""
        if not isinstance(text_el, dict) or not isinstance(shape_el, dict):
            return False
        tx, ty, tw, th = text_el.get('x'), text_el.get('y'), text_el.get('width'), text_el.get('height')
        sx, sy, sw, sh = shape_el.get('x'), shape_el.get('y'), shape_el.get('width'), shape_el.get('height')
        if None in (tx, ty, tw, th, sx, sy, sw, sh):
            return False
        return sx <= tx <= sx + sw and sy <= ty <= sy + sh

    # Extrair nós
    id_to_small = {}
    node_elements = [el for el in elements 
                     if el.get('type') in ('rectangle', 'ellipse', 'diamond') and el.get('id')]
    node_elements_by_id = {el['id']: el for el in node_elements}
    
    for idx, node_el in enumerate(node_elements, start=1):
        orig_id = node_el['id']
        small_id = f"n{idx}"
        id_to_small[orig_id] = small_id
        
        # Buscar texto do nó: primeiro em containerId, depois sobreposto
        node_text = next(
            ((el.get('text', '') or '').replace('\n', ' ').strip()
             for el in elements
             if el.get('type') == 'text' and el.get('containerId') == orig_id),
            None
        )
        
        if not node_text:
            node_text = next(
                ((el.get('text', '') or '').replace('\n', ' ').strip()
                 for el in elements
                 if el.get('type') == 'text' and not el.get('containerId')
                 and is_point_inside_shape(el, node_el) and (el.get('text', '') or '').strip()),
                str(_('Não foi possível identificar o conceito no mapa extraído'))
            )
        
        nodes[small_id] = node_text
    
    # Extrair arestas
    arrow_elements = [el for el in elements if el.get('type') in ('arrow', 'line') and el.get('id')]
    
    for edge_idx, arrow in enumerate(arrow_elements, start=1):
        start_binding = arrow.get('startBinding')
        end_binding = arrow.get('endBinding')
        
        # Tentar obter IDs do binding, senão auto-ligar a forma próxima
        start_id = start_binding.get('elementId') if start_binding else None
        end_id = end_binding.get('elementId') if end_binding else None
        
        if not start_id:
            start_id = nearest_shape(arrow_point_absolute(arrow, 0), node_elements_by_id)
        
        if not end_id:
            end_id = nearest_shape(arrow_point_absolute(arrow, -1), node_elements_by_id,
                                   exclude_ids=[start_id] if start_id else None)
        
        if not (start_id and end_id):
            continue
        
        from_small = id_to_small.get(start_id)
        to_small = id_to_small.get(end_id)
        
        if not (from_small and to_small):
            continue
        
        # Buscar label da aresta
        edge_label = next(
            ((el.get('text', '') or '').replace('\n', ' ').strip()
             for el in elements
             if el.get('type') == 'text' and el.get('containerId') == arrow['id']),
            "→"
        )
        
        edges.append((from_small, to_small, edge_label))
    
    # Salvar em arquivo (opcional, para debug)
    os.makedirs('media', exist_ok=True)
    mapa_grafo_path = 'media/mapa_grafo.txt'
    try:
        with open(mapa_grafo_path, 'w', encoding='utf-8') as f:
            f.write("nodes = {\n")
            for key, value in nodes.items():
                f.write(f'    "{key}": "{value}",\n')
            f.write("}\n\nedges = [\n")
            for edge in edges:
                f.write(f'    ("{edge[0]}", "{edge[1]}", "{edge[2]}"),\n')
            f.write("]\n")
    except Exception:
        pass
    
    # Deletar arquivo após criação
    try:
        if os.path.exists(mapa_grafo_path):
            os.remove(mapa_grafo_path)
    except Exception:
        pass
    
    return nodes, edges

def extract_proposition_from_map_with_style(map_path):
    """
    Extrai nós, arestas e estilos de um mapa conceitual Excalidraw.
    
    Preserva metadados visuais (cores, formas, estilos) do mapa original.
    Auto-liga pontas de seta soltas a formas geométricas próximas.
    
    Returns:
        tuple: (nodes, edges, node_styles, edge_styles)
    """
    nodes = {}
    edges = []
    node_styles = {}
    edge_styles = []

    try:
        with open(map_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return nodes, edges, node_styles, edge_styles

    if not isinstance(data, dict):
        return nodes, edges, node_styles, edge_styles

    elements = data.get('elements', [])
    if not elements:
        return nodes, edges, node_styles, edge_styles

    # Funções auxiliares
    def shape_center(shape_el):
        """Calcula o centro de uma forma."""
        if not isinstance(shape_el, dict):
            return None
        x, y, w, h = shape_el.get('x'), shape_el.get('y'), shape_el.get('width'), shape_el.get('height')
        return (x + w, y + h) if None not in (x, y, w, h) else None

    def arrow_point_absolute(arrow_el, point_idx):
        """Converte ponto relativo de seta para coordenadas absolutas."""
        points = arrow_el.get('points') or []
        if not points or abs(point_idx) >= len(points):
            return None
        point = points[point_idx]
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        ax, ay = arrow_el.get('x'), arrow_el.get('y')
        return (ax + point[0], ay + point[1]) if None not in (ax, ay) else None

    def nearest_shape(point, shapes_map, exclude_ids=None):
        """Encontra forma geométrica mais próxima (< 100px)."""
        if point is None:
            return None
        exclude_ids = set(exclude_ids or [])
        nearest, min_dist = None, 100.0
        for shape_id, shape_el in shapes_map.items():
            if shape_id in exclude_ids:
                continue
            center = shape_center(shape_el)
            if center:
                dist = ((center[0] - point[0])**2 + (center[1] - point[1])**2)**0.5
                if dist < min_dist:
                    min_dist = dist
                    nearest = shape_id
        return nearest

    def is_point_inside_shape(text_el, shape_el):
        """Verifica se texto está dentro de forma."""
        if not isinstance(text_el, dict) or not isinstance(shape_el, dict):
            return False
        tx, ty, tw, th = text_el.get('x'), text_el.get('y'), text_el.get('width'), text_el.get('height')
        sx, sy, sw, sh = shape_el.get('x'), shape_el.get('y'), shape_el.get('width'), shape_el.get('height')
        if None in (tx, ty, tw, th, sx, sy, sw, sh):
            return False
        return sx <= tx <= sx + sw and sy <= ty <= sy + sh

    # Setup
    elements_by_id = {el.get('id'): el for el in elements if el.get('id')}

    def normalize_color(color_value):
        return '#ffffff' if not color_value or str(color_value).lower() == 'transparent' else color_value

    def resolve_stroke_style(element):
        if not isinstance(element, dict):
            return 'solid'
        container_id = element.get('containerId')
        container = elements_by_id.get(container_id) if container_id else None
        return (container.get('strokeStyle') if container else None) or element.get('strokeStyle') or 'solid'

    # Índice de textos
    text_by_container = {}
    for el in elements:
        if el.get('type') != 'text':
            continue
        container_id = el.get('containerId')
        clean_text = (el.get('text', '') or '').replace('\n', ' ').strip()
        if clean_text and container_id:
            text_by_container[container_id] = clean_text

    text_by_position = {}
    free_text = [el for el in elements if el.get('type') == 'text' and not el.get('containerId')]
    for text_el in free_text:
        clean_text = (text_el.get('text', '') or '').replace('\n', ' ').strip()
        if not clean_text:
            continue
        containing = [n for n in elements 
                      if n.get('type') in ('rectangle', 'ellipse', 'diamond') 
                      and n.get('id') and is_point_inside_shape(text_el, n)]
        if containing:
            containing.sort(key=lambda n: ((n.get('width') or 0)*(n.get('height') or 0), n.get('x') or 0, n.get('y') or 0))
            text_by_position[containing[0]['id']] = clean_text

    # Extrair nós
    id_to_small = {}
    node_elements = [el for el in elements 
                     if el.get('type') in ('rectangle', 'ellipse', 'diamond') and el.get('id')]
    node_elements_by_id = {el['id']: el for el in node_elements}

    for idx, node_el in enumerate(node_elements, start=1):
        orig_id = node_el['id']
        small_id = f"n{idx}"
        id_to_small[orig_id] = small_id
        
        node_text = (text_by_container.get(orig_id) or 
                    text_by_position.get(orig_id) or 
                    str(_('Não foi possível identificar o conceito no mapa extraído')))
        nodes[small_id] = node_text

        node_styles[small_id] = {
            'original_id': orig_id,
            'shape': node_el.get('type', 'rectangle'),
            'stroke_color': node_el.get('strokeColor'),
            'background_color': normalize_color(node_el.get('backgroundColor')),
            'fill_style': node_el.get('fillStyle'),
            'stroke_style': resolve_stroke_style(node_el),
            'x': node_el.get('x'),
            'y': node_el.get('y'),
            'roughness': node_el.get('roughness'),
            'opacity': node_el.get('opacity'),
        }

    # Extrair arestas
    arrow_elements = [el for el in elements if el.get('type') in ('arrow', 'line') and el.get('id')]

    for edge_idx, arrow in enumerate(arrow_elements, start=1):
        start_binding = arrow.get('startBinding')
        end_binding = arrow.get('endBinding')

        start_id = start_binding.get('elementId') if start_binding else None
        end_id = end_binding.get('elementId') if end_binding else None

        if not start_id:
            start_id = nearest_shape(arrow_point_absolute(arrow, 0), node_elements_by_id)
        
        if not end_id:
            end_id = nearest_shape(arrow_point_absolute(arrow, -1), node_elements_by_id,
                                   exclude_ids=[start_id] if start_id else None)

        if not (start_id and end_id):
            continue

        from_small = id_to_small.get(start_id)
        to_small = id_to_small.get(end_id)
        if not (from_small and to_small):
            continue

        edge_label = text_by_container.get(arrow['id'], "→")
        edges.append((from_small, to_small, edge_label))

        edge_styles.append({
            'source': from_small,
            'target': to_small,
            'label': edge_label,
            'stroke_color': arrow.get('strokeColor'),
            'stroke_style': resolve_stroke_style(arrow),
            'x': arrow.get('x'),
            'y': arrow.get('y'),
            'roundness': arrow.get('roundness'),
            'start_arrowhead': arrow.get('startArrowhead'),
            'end_arrowhead': arrow.get('endArrowhead'),
            'opacity': arrow.get('opacity'),
        })

    return nodes, edges, node_styles, edge_styles

def extract_proposition_from_map_cmaptools(map_path):
    import xml.etree.ElementTree as ET
    
    nodes = {}
    edges = []
    node_styles = {}
    edge_styles = []

    def to_tag(element_name):
        return f'.//{{http://cmap.ihmc.us/xml/cmap/}}{element_name}'

    def parse_color(value):
        if not value:
            return None
        try:
            parts = [int(p.strip()) for p in str(value).split(',')]
            if len(parts) >= 3:
                r, g, b = parts[0], parts[1], parts[2]
                a = parts[3] if len(parts) > 3 else 255
                if a <= 0:
                    return 'transparent'
                return f'#{r:02x}{g:02x}{b:02x}'
        except Exception:
            return None
        return None

    def map_border_shape(shape_value):
        shape = (shape_value or '').strip().lower()
        if shape in ('oval', 'ellipse'):
            return 'ellipse'
        if shape in ('diamond',):
            return 'diamond'
        return 'rectangle'

    def map_border_style(style_value):
        style = (style_value or '').strip().lower()
        if style in ('dashed', 'dotted', 'solid'):
            return style
        return 'solid'

    def map_arrowhead(arrowhead_value):
        arrow = (arrowhead_value or '').strip().lower()
        if arrow in ('if-to-concept', 'arrow', 'triangle'):
            return 'triangle'
        if arrow in ('none',):
            return 'none'
        return 'triangle'

    def parse_scaled_coordinate(value):
        if value is None or value == '':
            return None
        try:
            return float(value) * 2
        except (TypeError, ValueError):
            return None
    
    try:
        tree = ET.parse(map_path)
        root = tree.getroot()
    except Exception as e:
        return nodes, edges, node_styles, edge_styles
    
    # Namespace do CmapTools
    ns = {'cmap': 'http://cmap.ihmc.us/xml/cmap/'}
    # Tentar sem namespace também (alguns arquivos não têm)
    
    # Extrair conceitos
    concepts = {}
    concept_list = root.find('.//concept-list')
    if concept_list is None:
        concept_list = root.find(to_tag('concept-list'))
    
    if concept_list is not None:
        for concept in concept_list.findall('concept'):
            concept_id = concept.get('id')
            label = concept.get('label', '')
            if concept_id:
                concepts[concept_id] = label
    
    # Extrair linking phrases (palavras de ligação)
    linking_phrases = {}
    lp_list = root.find('.//linking-phrase-list')
    if lp_list is None:
        lp_list = root.find(to_tag('linking-phrase-list'))
    
    if lp_list is not None:
        for lp in lp_list.findall('linking-phrase'):
            lp_id = lp.get('id')
            label = lp.get('label', '')
            if lp_id:
                linking_phrases[lp_id] = label
    
    # Extrair conexões
    connections = []
    conn_list = root.find('.//connection-list')
    if conn_list is None:
        conn_list = root.find(to_tag('connection-list'))
    
    if conn_list is not None:
        for conn in conn_list.findall('connection'):
            from_id = conn.get('from-id')
            to_id = conn.get('to-id')
            if from_id and to_id:
                connections.append((from_id, to_id))
    
    # Aparencia de conceitos (coordenadas e estilo)
    concept_appearance_map = {}
    concept_appearance_list = root.find('.//concept-appearance-list')
    if concept_appearance_list is None:
        concept_appearance_list = root.find(to_tag('concept-appearance-list'))

    if concept_appearance_list is not None:
        for appearance in list(concept_appearance_list):
            concept_id = appearance.get('id')
            if concept_id:
                concept_appearance_map[concept_id] = appearance.attrib

    # Aparencia de conexoes
    connection_appearance_map = {}
    connection_appearance_list = root.find('.//connection-appearance-list')
    if connection_appearance_list is None:
        connection_appearance_list = root.find(to_tag('connection-appearance-list'))

    if connection_appearance_list is not None:
        for appearance in list(connection_appearance_list):
            conn_id = appearance.get('id')
            if conn_id:
                connection_appearance_map[conn_id] = appearance.attrib

    # Defaults do style-sheet
    default_concept_style = {}
    default_connection_style = {}
    style_sheet_list = root.find('.//style-sheet-list')
    if style_sheet_list is None:
        style_sheet_list = root.find(to_tag('style-sheet-list'))

    if style_sheet_list is not None:
        for style_sheet in list(style_sheet_list):
            if style_sheet.get('id') == '_Default_':
                concept_style = style_sheet.find('concept-style')
                if concept_style is None:
                    concept_style = style_sheet.find(to_tag('concept-style'))
                if concept_style is not None:
                    default_concept_style = dict(concept_style.attrib)

                connection_style = style_sheet.find('connection-style')
                if connection_style is None:
                    connection_style = style_sheet.find(to_tag('connection-style'))
                if connection_style is not None:
                    default_connection_style = dict(connection_style.attrib)
                break

    id_to_simple = {}
    for idx, (concept_id, label) in enumerate(concepts.items(), start=1):
        simple_id = f"n{idx}"
        id_to_simple[concept_id] = simple_id
        nodes[simple_id] = label

        concept_appearance = concept_appearance_map.get(concept_id, {})
        combined_concept_style = {
            **default_concept_style,
            **concept_appearance,
        }

        node_styles[simple_id] = {
            'original_id': concept_id,
            'shape': map_border_shape(combined_concept_style.get('border-shape')),
            'stroke_color': parse_color(combined_concept_style.get('border-color')),
            'background_color': parse_color(combined_concept_style.get('background-color')),
            'fill_style': 'solid',
            'stroke_style': map_border_style(combined_concept_style.get('border-style')),
            'x': parse_scaled_coordinate(combined_concept_style.get('x')),
            'y': parse_scaled_coordinate(combined_concept_style.get('y')),
            'roughness': None,
            'opacity': None,
        }
    
    # Construir grafo de conexões para identificar proposições
    # Uma proposição típica é: concept -> linking-phrase -> concept
    graph = {}
    for from_id, to_id in connections:
        if from_id not in graph:
            graph[from_id] = []
        graph[from_id].append(to_id)
    
    # Identificar proposições: concept1 -> linking-phrase -> concept2
    connection_by_from_to = {
        (conn.get('from-id'), conn.get('to-id')): conn
        for conn in (list(conn_list) if conn_list is not None else [])
        if conn.get('from-id') and conn.get('to-id')
    }

    for from_id, to_id in connections:
        # Verificar se from_id é um conceito e to_id é uma linking-phrase
        if from_id in concepts and to_id in linking_phrases:
            # Procurar onde a linking-phrase conecta
            if to_id in graph:
                for final_id in graph[to_id]:
                    if final_id in concepts:
                        # Temos uma proposição: concept1 -> lp -> concept2
                        from_simple = id_to_simple.get(from_id)
                        to_simple = id_to_simple.get(final_id)
                        edge_label = linking_phrases[to_id]
                        
                        if from_simple and to_simple:
                            edges.append((from_simple, to_simple, edge_label))
                            conn_a = connection_by_from_to.get((from_id, to_id))
                            conn_b = connection_by_from_to.get((to_id, final_id))
                            conn_app_a = connection_appearance_map.get(conn_a.get('id')) if conn_a is not None else {}
                            conn_app_b = connection_appearance_map.get(conn_b.get('id')) if conn_b is not None else {}
                            combined_connection_style = {
                                **default_connection_style,
                                **(conn_app_a or {}),
                                **(conn_app_b or {}),
                            }

                            edge_styles.append({
                                'source': from_simple,
                                'target': to_simple,
                                'label': edge_label,
                                'stroke_color': parse_color(combined_connection_style.get('color')),
                                'stroke_style': map_border_style(combined_connection_style.get('style')),
                                'x': None,
                                'y': None,
                                'roundness': None,
                                'start_arrowhead': 'none',
                                'end_arrowhead': map_arrowhead(combined_connection_style.get('arrowhead')),
                                'opacity': None,
                            })
    
    return nodes, edges, node_styles, edge_styles

def extract_text_from_pdf(pdf_path):
    """
    Extrai texto de um PDF usando pdfplumber.
    Se não conseguir extrair texto suficiente, usa OCR como fallback.
    """
    text = ""
    
    # Tentativa 1: Extrair com pdfplumber (PDFs com texto selecionável)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text
        
        # Verificar se extraiu texto suficiente
        if len(text.strip()) > 50:  # Se tiver pelo menos 50 caracteres
            return text
    except Exception as e:
        return ""
    
    # Tentativa 2: OCR (para PDFs escaneados ou com imagens)
    if not OCR_AVAILABLE:
        return text
    
    try:
        # Converter PDF para imagens
        images = convert_from_path(pdf_path, dpi=300)
        
        ocr_text = ""
        for i, image in enumerate(images, 1):
            # Extrair texto da imagem usando Tesseract
            page_text = pytesseract.image_to_string(image, lang='por+eng')
            ocr_text += page_text + "\n"
        
        if len(ocr_text.strip()) > 0:
            return ocr_text
        else:
            return text  # Retorna o texto parcial do pdfplumber
            
    except Exception as e:
        return text  # Retorna o texto parcial do pdfplumber em caso de erro

def find_missing_relations(edges_aluno, nodes_aluno, document_propositions):
    connections = suggest_connections(edges_aluno, nodes_aluno, document_propositions)
    
    # Garantir que connections é um dicionário
    if not isinstance(connections, dict):
        connections = {"nodes": {}, "edges": []}
    
    return {
        'new_nodes': connections.get('nodes', {}),
        'conexoes_faltantes': connections.get('edges', [])
    }

def prepare_structure_graph_data(rubric_classification, concept_map, suggested_connections_data):
    node_colors = rubric_classification.node_colors or {}
    node_styles = concept_map.node_styles_data or {}
    edge_styles = concept_map.edge_styles_data or []
    
    # Edges existentes no mapa do aluno
    existing_edges = []
    for idx, e in enumerate(concept_map.edges_data):
        existing_edges.append({
            'source': e[0],
            'target': e[1],
            'label': e[2] if len(e) > 2 else '',
            'type': 'existing',  # Marca como existente
            'style': edge_styles[idx] if idx < len(edge_styles) else None,
        })
    
    # Edges sugeridas (conexões faltantes)
    suggested_edges = []
    suggested_nodes = suggested_connections_data.get('new_nodes', {})
    added_suggested_nodes = []
    for connection in suggested_connections_data.get('conexoes_faltantes', []):
        # if connection['source'][0] == 'n' or connection['target'][0] == 'n': # check if either sorce or target is an existing node (starts with 'n')
        added_suggested_nodes.append(connection['source'])
        added_suggested_nodes.append(connection['target'])
        suggested_edges.append({
            'source': connection['source'],
            'target': connection['target'],
            'label': connection['label'],
            'type': 'suggested',  # Marca como sugestão
            'style': None,
        })
    
    nodes = []
    for nid, ntxt in concept_map.nodes_data.items():
        nodes.append({
            'id': nid,
            'label': ntxt,
            'color': node_colors.get(nid, 'white'),
            'style': node_styles.get(nid),
        })
        
    for new_nid, new_ntxt in suggested_nodes.items():
        if new_nid in added_suggested_nodes:
            nodes.append({
                'id': new_nid,
                'label': new_ntxt,
                'color': "green",
                'style': None,
            })
    
    return {
        'nodes': nodes,
        'edges': existing_edges + suggested_edges
    }

def call_external_api_for_classification(prompt, type_thinking='disabled'):
    api_key = getattr(settings, 'DEEPSEEK_API_KEY', None)
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    
    try:
        messages = [{"role": "system", "content": "You are an expert in educational content analysis."}, {"role": "user", "content": prompt}]
        if type_thinking == 'enabled':
            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                reasoning_effort="high",
                extra_body={"thinking": {"type": 'enabled'}},
                messages=messages,
                stream=False
            )
        else:
            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=messages,
                extra_body={"thinking": {"type": 'disabled'}},
                temperature=1,
                stream=False
            )
        
        content = response.choices[0].message.content
        return "success", json.loads(content)
    except Exception as e:
        print(f"Error calling external API: {e}", flush=True)
        return "error", e
    
def extract_essential_topics_from_reference_document(reference_document_full_text):
    prompt = f"""Construct a concept map based on the content of the file.
    {reference_document_full_text}

    You may use five steps to do it, but only show the results of the step (5) and nothing more. Show the results in the original language of the file.

    (1) Section Segmentation: break down the material into units that facilitate the concept extraction.
    (2) Key Concept Extraction From Each Section: identify the main concept within the specified unit
    (3) Relationship Identification: map relationships between concepts
    (4) Merge and refine: merge and refine the results of each section to create a concept map
    (5) Show only the results in the following format: present the results in the following format: ["concept_1 <relation> concept_2", "concept_3 <relation> concept_4", ...]
    """
    count = 0
    while True:
        count += 1
        flag, response = call_external_api_for_classification(prompt, type_thinking='disabled')
        if flag == "success":
            try:
                nodes, edges = set(), []
                for item in response:
                    parts = item.split(" <")
                    if len(parts) == 2:
                        concept1 = parts[0].strip()
                        relation = parts[1].split(">")[0].strip()
                        concept2 = parts[1].split(">")[1].strip()
                        
                        nodes.add(concept1)
                        nodes.add(concept2)
                        edges.append([concept1, concept2, relation])
                return list(nodes), edges
            except Exception as e:
                continue
        if count > 10:
            return response
    

def classify_propositions(concept_map_propositions, reference_document_propositions):
    proposition_list = "\n".join(concept_map_propositions)
    proposition_ref_list = "\n".join(reference_document_propositions)

    prompt = f"""Given the list of propositions below:
    {proposition_list}

    You may match each proposition to the most similar topic on the list below and provide a similarity score between 0 and 1:
    {proposition_ref_list}

    Use the following steps to do this match, but present the results only on step 3:
    1. Topic and proposition linking: compare the proposition with every topic from the list provided and determine their similarity score
    2. Review information: match all the proposition and topics with a similarity score above 0.7, and leave it as an empty list if no topic is similar enough
    3. Present the results: show only the final classifications in the following format [["<Proposition>", ["similar topic 1", "similar topic 2", ...], [similarity score 1, similarity score 2, ...]], [...]]"""
    
    count = 0
    while True:
        count += 1
        flag, response = call_external_api_for_classification(prompt, type_thinking='disabled')
        if flag == "success":
            return response
        if count > 10:
            return response
        
def correct_propositions(concept_map_propositions, reference_document_full_text):
    proposition_list = "\n".join(concept_map_propositions)
        
    prompt = f"""Classify the list of propositions extracted from a concept map.
    {proposition_list}

    You may classify if the propositions are 'Correct', 'Incorrect' or 'Partially Correct' according to the reference document:
    {reference_document_full_text}

    Use the following steps to do this classification, but present the results only on step 3:
    1. Proposition correction: classify the propositions, in 'Correct', 'Incorrect' or 'Partially Correct', according the reference document provided and provide a justification for each classification in the same language as the propositions
    2. Review information: refine the results and review the classifications done, in either 'Correct', 'Incorrect' or 'Partially Correct', and the justifications given
    3. Present the results: show only the final classifications in the following format [["Proposition", "Classification", "justification"], [...]]"""
    
    count = 0
    while True:
        count += 1
        flag, response = call_external_api_for_classification(prompt, type_thinking='disabled')
        if flag == "success":
            return response
        if count > 10:
            return response
        

def suggest_connections(concept_map_edges, concept_map_nodes, reference_document_propositions):
    prompt = f"""based on this propositions extracted from a reference document:
    {reference_document_propositions}
    and the structure of a concept map:
    nodes = {concept_map_nodes}
    edges = {concept_map_edges}

    Suggest connections and concepts that should be added to the concept map. 
    Every new concept recommended should be linked to an existing node from the concept map. You can recommend new connection on already existing concepts.
    Show only the answer in the following format without any additional text:
    {{"nodes": {{"m1": "new concept 1", "m2": "new concept 2"}}, "edges": [ {{"source": "n1", "target": "m1", "label": "connection label"}}, ...] }}"""
    count = 0
    while True:
        count += 1
        flag, response = call_external_api_for_classification(prompt, type_thinking='disabled')
        if flag == "success":
            return response
        if count > 10:
            return response

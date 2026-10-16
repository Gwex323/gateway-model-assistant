"""Conservative chat/image-input recognition; no inference endpoint calls."""
import re


def chat_metadata_support(metadata):
    """Read bounded, allow-listed declarations; conflicting negatives win."""
    declarations = []
    inputs, outputs = set(), set()
    output_declarations = []
    seen = set()
    chat_types = {'chat', 'chat-model', 'chat-completion', 'chat-completions',
                  'conversational', 'text-generation', 'image-text-to-text', 'video-text-to-text'}
    other_types = {'embedding', 'embeddings', 'text-embedding', 'feature-extraction',
                   'rerank', 'reranker', 'reranking', 'image-generation', 'text-to-image',
                   'image-to-image', 'video-generation', 'text-to-video', 'image-to-video',
                   'tts', 'text-to-speech', 'speech-to-text', 'transcription',
                   'automatic-speech-recognition', 'audio-generation',
                   '3d-generation', '3d-model-generation', 'text-to-3d', 'image-to-3d',
                   'mesh-generation', 'texture-generation'}

    def strings(value):
        return {v.strip().lower() for v in value} if (
            isinstance(value, list) and value and all(isinstance(v, str) for v in value)
        ) else set()

    def visit(obj, depth=0):
        if not isinstance(obj, dict) or depth > 4 or id(obj) in seen:
            return
        seen.add(id(obj))
        for key in ('supports_chat', 'supportsChat', 'chat'):
            value = obj.get(key)
            if isinstance(value, dict):
                value = value.get('supported')
            if type(value) is bool:
                declarations.append(value)
        for key in ('model_type', 'modelType', 'task', 'pipeline_tag', 'type', 'mode'):
            value = obj.get(key)
            if isinstance(value, str):
                value = value.strip().lower().replace('_', '-')
                if value in chat_types or value in other_types:
                    declarations.append(value in chat_types)
        for key in ('supported_endpoints', 'supportedEndpoints', 'endpoints'):
            endpoints = strings(obj.get(key))
            paths = {v.strip('/').removeprefix('v1/').removeprefix('v1beta/') for v in endpoints}
            if paths & {'chat/completions', 'messages'}:
                declarations.append(True)
            elif paths and paths <= {'embeddings', 'rerank', 'images/generations', 'images/edits',
                                     'videos', 'audio/speech', 'audio/transcriptions', 'audio/translations'}:
                declarations.append(False)
        for key in ('input_modalities', 'inputModalities'):
            inputs.update(strings(obj.get(key)))
        for key in ('output_modalities', 'outputModalities'):
            value = strings(obj.get(key))
            outputs.update(value)
            output_declarations.append(value)
        modalities = obj.get('modalities')
        if isinstance(modalities, dict):
            inputs.update(strings(modalities.get('input')))
            value = strings(modalities.get('output'))
            outputs.update(value)
            output_declarations.append(value)
        modality = obj.get('modality')
        if isinstance(modality, str) and modality.count('->') == 1:
            left, right = modality.split('->')
            inputs.update(v.strip().lower() for v in left.split('+') if v.strip())
            value = {v.strip().lower() for v in right.split('+') if v.strip()}
            outputs.update(value)
            output_declarations.append(value)
        for key in ('capabilities', 'architecture', 'metadata', 'model_info', 'modelInfo'):
            visit(obj.get(key), depth + 1)

    visit(metadata)
    # Text output alone can also describe transcription or reranking. Only
    # infer chat from modalities when text is explicitly an accepted input too.
    nontext = {'image', 'images', 'video', 'audio', 'embedding', 'embeddings', 'vector', 'vectors',
               '3d', '3d-model', '3d_model', 'mesh', 'meshes', 'point-cloud', 'point_cloud',
               'pointcloud', 'geometry'}
    if any(value and value <= nontext for value in output_declarations):
        declarations.append(False)
    elif 'text' in inputs and 'text' in outputs:
        declarations.append(True)
    return all(declarations) if declarations else None


def chat_model_support(model_id, declared=None):
    """Metadata first, known names second, otherwise explicitly unknown."""
    if type(declared) is bool:
        return declared
    # A provider namespace may carry the only family identifier (kling/video-o1).
    # Keep token boundaries so unrelated chat aliases such as klingon are unknown.
    if re.search(r'(?:^|[/_.-])(?:kling(?:ai)?|(?:hy|hunyuan)[-_.]?3d)(?=$|[/_.-]|[0-9])',
                 model_id.casefold()):
        return False
    name = model_id.casefold().split('/')[-1]
    if re.search(
        r'(?:^|[-_.])(?:seedream|seedance|minimax-h3|dall-e|stable-diffusion|flux|'
        r'sora|imagen|gpt-image|image-generation|text-to-image|text-to-video|'
        r'image-to-video|video-generation|text-to-3d|image-to-3d|3d-generation|'
        r'3d-model-generation|mesh-generation|embedding|embeddings|embed|rerank|reranker|'
        r'whisper|tts|text-to-speech|speech-to-text)(?:$|[-_.0-9])', name,
    ) or (name.startswith('gemini-') and re.search(r'-image(?:-|$)', name)):
        return False
    if image_input_support(model_id) is True or re.match(
        r'^(?:gpt-(?:3\.5|4|5)(?:[.o-]|$)|claude-(?:3|sonnet|opus|haiku)|'
        r'deepseek-(?:chat|reasoner|coder)(?:-|$)|'
        r'qwen(?:2(?:\.5)?|3)(?:[.-]|$))', name,
    ):
        return True
    return None

# Model-family reference URLs:
# https://developers.openai.com/api/docs/models/gpt-4.1
# https://platform.claude.com/docs/en/build-with-claude/vision
# https://ai.google.dev/gemini-api/docs/image-understanding

def image_input_support(model_id, metadata=None):
    declarations = []
    def visit(obj, depth=0):
        if not isinstance(obj, dict) or depth > 4:
            return
        for key in ('supportsImages', 'supports_images', 'supports_vision', 'vision'):
            value = obj.get(key)
            if isinstance(value, dict):
                value = value.get('supported')
            if type(value) is bool:
                declarations.append(value)
        for key in ('input_modalities', 'inputModalities'):
            values = obj.get(key)
            if isinstance(values, list) and values and all(isinstance(v, str) for v in values):
                declarations.append(any(v.lower() in ('image', 'images', 'vision') for v in values))
        modality = obj.get('modality')
        if isinstance(modality, str) and '->' in modality:
            inputs = modality.split('->', 1)[0].split('+')
            declarations.append(any(v.strip().lower() in ('image', 'images') for v in inputs))
        modalities = obj.get('modalities')
        if isinstance(modalities, dict) and isinstance(modalities.get('input'), list):
            visit({'input_modalities': modalities['input']}, depth + 1)
        for key in ('capabilities', 'architecture', 'metadata', 'model_info'):
            visit(obj.get(key), depth + 1)
    visit(metadata)
    if declarations:
        return all(declarations)  # An explicit negative wins conflicting declarations.
    mid = model_id.lower().split('/')[-1]
    if any(word in mid for word in ('embedding', 'rerank', 'tts', 'image-generation', 'gpt-image', 'seedream', 'seedance', 'sora')):
        return None
    if re.fullmatch(r'gpt-(?:4o(?:-mini)?|4\.1(?:-mini|-nano)?)(?:-\d{4}-\d{2}-\d{2})?', mid):
        return True
    if re.fullmatch(r'claude-(?:3(?:[.-][57])?-(?:sonnet|haiku|opus)|(?:sonnet|opus|haiku)-4(?:[.-][0-9]+)?)(?:-\d{8}|-latest)?', mid):
        return True
    if re.fullmatch(r'gemini-(?:1\.5|2\.0|2\.5)-(?:pro|flash|flash-lite)(?:-latest|-\d{3}|-preview(?:-\d{2}-\d{2})?)?', mid):
        return True
    # Explicit vision/VL model names are a declaration, not a generic 'image' match.
    if re.search(r'(?:^|[-_])(?:vision|vl)(?:$|[-_])', mid):
        return True
    return None

"""Conservative image-input recognition; never calls a generation endpoint."""
import re

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

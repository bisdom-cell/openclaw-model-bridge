# Example Provider Plugin (Python format)
#
# Use Python plugins when you need:
#   - Custom authentication (non-standard headers)
#   - Special request/response handling
#   - Dynamic model discovery
#
# Requirements:
#   - Define exactly ONE class inheriting from BaseProvider
#   - Set all required class attributes (name, base_url, api_key_env, models)
#   - File must NOT start with '_' or '.' to be auto-discovered
#
# To use: copy this file, remove the leading '_', and customize.
#
# from providers import BaseProvider, ModelInfo, ProviderCapabilities
#
#
# class MyCustomProvider(BaseProvider):
#     name = "custom"
#     display_name = "My Custom Provider"
#     base_url = "https://api.custom.example.com/v1"
#     api_key_env = "CUSTOM_API_KEY"
#     auth_style = "custom"
#     models = [
#         ModelInfo(
#             model_id="custom-v1",
#             display_name="Custom Model V1",
#             modalities=["text"],
#             context_window=32768,
#             max_output_tokens=4096,
#             is_default=True,
#         ),
#     ]
#     capabilities = ProviderCapabilities(
#         text=True,
#         tool_calling=True,
#         streaming=True,
#     )
#
#     def make_auth_headers(self, api_key: str):
#         """Custom authentication — e.g., HMAC signing."""
#         import hashlib, time
#         timestamp = str(int(time.time()))
#         signature = hashlib.sha256(f"{api_key}:{timestamp}".encode()).hexdigest()
#         return {
#             "X-Api-Key": api_key,
#             "X-Timestamp": timestamp,
#             "X-Signature": signature,
#         }

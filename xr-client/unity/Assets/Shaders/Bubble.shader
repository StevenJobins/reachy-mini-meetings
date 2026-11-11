// Speech bubble parts: a rounded rectangle, drawn in the fragment shader so it stays sharp at any size.
// _Rect = width, height, corner radius (metres). Always on top of the video, like the status text.
// _Border / _BorderW: a line of that colour and width (metres) along the inside of the edge; width 0 = none.
// With a clear _Color it is an outline only (the frame around a speaker's face).
Shader "Reachy/Bubble"
{
    Properties
    {
        _Color ("Color", Color) = (1, 1, 1, 1)
        _Rect ("Size and radius", Vector) = (1, 1, 0.05, 0)
        _Border ("Border", Color) = (1, 1, 1, 0)
        _BorderW ("Border width", Float) = 0
    }
    SubShader
    {
        Tags { "RenderType" = "Transparent" "Queue" = "Transparent+10" "RenderPipeline" = "UniversalPipeline" }
        Pass
        {
            Blend SrcAlpha OneMinusSrcAlpha
            ZWrite Off
            ZTest Always
            Cull Off

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            CBUFFER_START(UnityPerMaterial)
                half4 _Color;
                float4 _Rect;
                half4 _Border;
                float _BorderW;
            CBUFFER_END

            Varyings vert(Attributes v)
            {
                Varyings o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.positionCS = TransformObjectToHClip(v.positionOS.xyz);
                o.uv = v.uv;
                return o;
            }

            half4 frag(Varyings i) : SV_Target
            {
                float2 p = (i.uv - 0.5) * _Rect.xy;
                float2 q = abs(p) - _Rect.xy * 0.5 + _Rect.z;
                float d = length(max(q, 0)) + min(max(q.x, q.y), 0) - _Rect.z;
                float aa = max(fwidth(d), 1e-5);
                half inside = 1 - smoothstep(-aa, aa, d);
                half edge = _BorderW > 0 ? smoothstep(-_BorderW - aa, -_BorderW + aa, d) * _Border.a : 0;
                half4 c = half4(lerp(_Color.rgb, _Border.rgb, edge), lerp(_Color.a, 1, edge));
                return half4(c.rgb, c.a * inside);
            }
            ENDHLSL
        }
    }
}

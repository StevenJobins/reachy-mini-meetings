// The robot video on its piece of sphere. ZTest Always: the sphere is further away than the floor below it.
Shader "Reachy/VideoSphere"
{
    Properties
    {
        _MainTex ("Video", 2D) = "white" {}
        _Color ("Color", Color) = (1, 1, 1, 1)
        _Sharpen ("Sharpen", Range(0, 2)) = 0.7
    }
    SubShader
    {
        Tags { "RenderType" = "Transparent" "Queue" = "Geometry+100" "RenderPipeline" = "UniversalPipeline" }
        Pass
        {
            Blend SrcAlpha OneMinusSrcAlpha   // soft edges: the live picture blends into the room panorama
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

            TEXTURE2D(_MainTex);
            SAMPLER(sampler_MainTex);

            CBUFFER_START(UnityPerMaterial)
                float4 _MainTex_ST;
                half4 _Color;
                float4 _MainTex_TexelSize;
                half _Sharpen;
            CBUFFER_END

            Varyings vert(Attributes v)
            {
                Varyings o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.positionCS = TransformObjectToHClip(v.positionOS.xyz);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            half4 frag(Varyings i) : SV_Target
            {
                float2 e = min(i.uv, 1 - i.uv);
                float edge = smoothstep(0, 0.03, e.x) * smoothstep(0, 0.04, e.y);
                // Unsharp mask: the 1080p picture is spread over a wide angle and softened by the video coding; adding
                // back the difference to the four neighbours makes edges (faces, text on a screen) crisper.
                float2 d = _MainTex_TexelSize.xy;
                half3 c = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv).rgb;
                half3 around = (SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv + float2(d.x, 0)).rgb
                              + SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv - float2(d.x, 0)).rgb
                              + SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv + float2(0, d.y)).rgb
                              + SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv - float2(0, d.y)).rgb) * 0.25;
                c = saturate(c + _Sharpen * (c - around));
                return half4(c * _Color.rgb, edge);
            }
            ENDHLSL
        }
    }
}

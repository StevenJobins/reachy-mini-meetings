// Frame rate and frame time, averaged over 0.5 s, as world-space text.
// textMaterial uses Reachy/WorldText (set by ProjectConfigure); it is an asset so the shader ends up in the build.

using UnityEngine;

public class FpsLabel : MonoBehaviour
{
    const float Interval = 0.5f;

    public Material textMaterial;

    TextMesh text;
    Material material;
    Font font;
    int frames;
    float elapsed;

    void Awake()
    {
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        material = new Material(textMaterial);
        UpdateAtlas(font);
        Font.textureRebuilt += UpdateAtlas;

        text = gameObject.AddComponent<TextMesh>();
        text.font = font;
        text.fontSize = 64;
        text.characterSize = 0.01f;
        text.anchor = TextAnchor.MiddleCenter;
        text.color = Color.white;
        text.text = "…";
        GetComponent<MeshRenderer>().sharedMaterial = material;
    }

    void OnDestroy() => Font.textureRebuilt -= UpdateAtlas;

    void UpdateAtlas(Font f)
    {
        if (f == font) material.mainTexture = font.material.mainTexture;
    }

    void Update()
    {
        frames++;
        elapsed += Time.unscaledDeltaTime;
        if (elapsed < Interval) return;
        text.text = $"{frames / elapsed:0} fps   {1000f * elapsed / frames:0.0} ms";
        frames = 0;
        elapsed = 0;
    }
}

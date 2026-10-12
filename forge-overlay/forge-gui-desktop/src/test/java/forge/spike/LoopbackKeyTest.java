package forge.spike;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * The OpenRouter bridge must never send OPENROUTER_API_KEY to a loopback endpoint (verifier BLOCKER,
 * 2026-09-07): the eval drivers auto-load secrets.env, so an operator running the acceptance suite's
 * loopback fake model would otherwise ship the real key over cleartext HTTP. The decision is a pure
 * function precisely so this can be asserted with a fake key without touching the environment.
 */
public class LoopbackKeyTest {
    @Test
    public void keyIsNeverSentToLoopback() {
        Assert.assertNull(LlmSpikeGui.OpenRouterBridge.authorizationHeader("sk-fake", "http://127.0.0.1:5555/v1"));
        Assert.assertNull(LlmSpikeGui.OpenRouterBridge.authorizationHeader("sk-fake", "http://localhost:5555/v1"));
        Assert.assertNull(LlmSpikeGui.OpenRouterBridge.authorizationHeader("sk-fake", "http://[::1]:5555/v1"));
    }

    @Test
    public void keyIsSentToARealEndpointAndOnlyWhenPresent() {
        Assert.assertEquals(LlmSpikeGui.OpenRouterBridge.authorizationHeader("sk-fake", "https://openrouter.ai/api/v1"), "Bearer sk-fake");
        Assert.assertNull(LlmSpikeGui.OpenRouterBridge.authorizationHeader(null, "https://openrouter.ai/api/v1"));
        Assert.assertNull(LlmSpikeGui.OpenRouterBridge.authorizationHeader("", "https://openrouter.ai/api/v1"));
        // An unset base URL means the real API: the key must go there, never be dropped.
        Assert.assertEquals(LlmSpikeGui.OpenRouterBridge.authorizationHeader("sk-fake", null), "Bearer sk-fake");
    }
}

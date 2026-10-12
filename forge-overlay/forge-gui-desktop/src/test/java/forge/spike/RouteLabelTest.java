package forge.spike;

import org.testng.Assert;
import org.testng.annotations.Test;

public class RouteLabelTest {
    @Test
    public void randomArmNeverClaimsToConsultAModel() {
        String label = LlmSpikeGui.PlayerControllerLLM.routeLabel("opus", true);
        Assert.assertFalse(label.contains("asking"));
        Assert.assertFalse(label.contains("opus"));
        Assert.assertTrue(label.contains("no model call"));
    }

    @Test
    public void modelArmPreservesReconciliationMarker() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.routeLabel("haiku", false), "asking haiku...");
    }
}

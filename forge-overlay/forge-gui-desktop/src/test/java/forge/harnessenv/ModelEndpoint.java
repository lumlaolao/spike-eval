package forge.harnessenv;

import java.nio.file.Path;

/** harness-env-abi/1: where the seat's model is. POST {baseUrl}/chat/completions, OpenAI-compatible, no key. */
public final class ModelEndpoint {
    public final String baseUrl;
    public final String model;
    public final Path logDir;

    public ModelEndpoint(String baseUrl, String model, Path logDir) {
        this.baseUrl = baseUrl;
        this.model = model;
        this.logDir = logDir;
    }
}

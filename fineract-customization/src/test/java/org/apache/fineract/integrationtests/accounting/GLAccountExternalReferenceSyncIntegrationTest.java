/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements. See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership. The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License. You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied. See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
package org.apache.fineract.integrationtests.accounting;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

import com.google.gson.Gson;
import io.restassured.builder.RequestSpecBuilder;
import io.restassured.builder.ResponseSpecBuilder;
import io.restassured.http.ContentType;
import io.restassured.specification.RequestSpecification;
import io.restassured.specification.ResponseSpecification;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import org.apache.fineract.integrationtests.common.Utils;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

public class GLAccountExternalReferenceSyncIntegrationTest {

    private static final String GL_ACCOUNT_URL = "/fineract-provider/api/v1/glaccounts?" + Utils.TENANT_IDENTIFIER;
    private static final String GL_ACCOUNT_ID_RESPONSE = "resourceId";

    private RequestSpecification requestSpec;
    private ResponseSpecification responseSpec200;
    private ResponseSpecification responseSpec409;
    private ResponseSpecification responseSpec401;

    @BeforeEach
    public void setup() {
        Utils.initializeRESTAssured();
        this.requestSpec = new RequestSpecBuilder().setContentType(ContentType.JSON).build();
        this.requestSpec.header("Authorization", "Basic " + Utils.loginIntoServerAndGetBase64EncodedAuthenticationKey());

        this.responseSpec200 = new ResponseSpecBuilder().expectStatusCode(200).build();
        this.responseSpec409 = new ResponseSpecBuilder().expectStatusCode(409).build();
        this.responseSpec401 = new ResponseSpecBuilder().expectStatusCode(401).build();
    }

    @Test
    public void testGLAccountSync_Behavior1_And_Behavior2_And_Behavior3() {
        final String externalRef = "GL-" + UUID.randomUUID().toString().substring(0, 8);
        final String glCode = "CODE-" + UUID.randomUUID().toString().substring(0, 8);

        final Map<String, Object> payload1 = new HashMap<>();
        payload1.put("name", "Asset Receivable Account");
        payload1.put("glCode", glCode);
        payload1.put("type", 1); // Asset
        payload1.put("usage", 1); // Detail
        payload1.put("manualEntriesAllowed", true);
        payload1.put("description", "Synced from ERP");
        payload1.put("external_reference", externalRef);

        final String json1 = new Gson().toJson(payload1);

        // Behavior 1: First creation succeeds and persists external_reference
        final Integer accountId1 = Utils.performServerPost(this.requestSpec, this.responseSpec200, GL_ACCOUNT_URL, json1,
                GL_ACCOUNT_ID_RESPONSE);
        assertNotNull(accountId1, "Account ID must not be null on creation");

        // Behavior 2: Identical payload returns existing account ID with HTTP 200 (no duplicate)
        final Integer accountId2 = Utils.performServerPost(this.requestSpec, this.responseSpec200, GL_ACCOUNT_URL, json1,
                GL_ACCOUNT_ID_RESPONSE);
        assertEquals(accountId1, accountId2, "Repeated call with identical payload must return the existing account ID");

        // Behavior 3: Same external_reference with different payload is rejected with HTTP 409 Conflict
        final Map<String, Object> payloadDiff = new HashMap<>(payload1);
        payloadDiff.put("name", "Conflicting Account Name");
        payloadDiff.put("glCode", "CONFLICT-" + UUID.randomUUID().toString().substring(0, 8));
        final String jsonDiff = new Gson().toJson(payloadDiff);

        Utils.performServerPost(this.requestSpec, this.responseSpec409, GL_ACCOUNT_URL, jsonDiff, "errors");
    }

    @Test
    public void testGLAccountSync_Behavior4_ProtectedByPermission() {
        final RequestSpecification unauthSpec = new RequestSpecBuilder().setContentType(ContentType.JSON).build();
        final Map<String, Object> payload = new HashMap<>();
        payload.put("name", "Unauthorized Test Account");
        payload.put("glCode", "UNAUTH-1");
        payload.put("type", 1);
        payload.put("usage", 1);
        payload.put("manualEntriesAllowed", true);

        Utils.performServerPost(unauthSpec, this.responseSpec401, GL_ACCOUNT_URL, new Gson().toJson(payload), "");
    }
}

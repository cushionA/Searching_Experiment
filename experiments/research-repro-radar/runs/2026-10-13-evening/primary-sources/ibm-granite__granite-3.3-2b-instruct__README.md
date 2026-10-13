---
pipeline_tag: text-generation
inference: false
license: apache-2.0
library_name: transformers
tags:
- language
- granite-3.3
base_model:
- ibm-granite/granite-3.3-2b-base
---

# Granite-3.3-2B-Instruct

**Model Summary:**
Granite-3.3-2B-Instruct is a 2-billion parameter 128K context length language model fine-tuned for improved reasoning and instruction-following capabilities. Built on top of Granite-3.3-2B-Base, the model delivers significant gains on benchmarks for measuring generic performance including AlpacaEval-2.0 and Arena-Hard, and improvements in mathematics, coding, and instruction following. It supports structured reasoning through \<think\>\<\/think\> and \<response\>\<\/response\> tags, providing clear separation between internal thoughts and final outputs. The model has been trained on a carefully balanced combination of permissively licensed data and curated synthetic tasks.


- **Developers:** Granite Team, IBM
- **GitHub Repository:** [ibm-granite/granite-3.3-language-models](https://github.com/ibm-granite/granite-3.3-language-models)
- **Website**: [Granite Docs](https://www.ibm.com/granite/docs/)
- **Release Date**: April 16th, 2025
- **License:** [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)

**Supported Languages:** 
English, German, Spanish, French, Japanese, Portuguese, Arabic, Czech, Italian, Korean, Dutch, and Chinese. However, users may finetune this Granite model for languages beyond these 12 languages.

**Intended Use:** 
This model is designed to handle general instruction-following tasks and can be integrated into AI assistants across various domains, including business applications.

**Capabilities**
* Thinking
* Summarization
* Text classification
* Text extraction
* Question-answering
* Retrieval Augmented Generation (RAG)
* Code related tasks
* Function-calling tasks
* Multilingual dialog use cases
<!-- * Fill-in-the-middle -->
* Long-context tasks including long document/meeting summarization, long document QA, etc.



**Generation:** 
This is a simple example of how to use Granite-3.3-2B-Instruct model.

Install the following libraries:

```shell
pip install torch torchvision torchaudio
pip install accelerate
pip install transformers
```
Then, copy the snippet from the section that is relevant for your use case.

```python
from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
import torch

model_path="ibm-granite/granite-3.3-2b-instruct"
device="cuda"
model = AutoModelForCausalLM.from_pretrained(
        model_path,
        device_map=device,
        torch_dtype=torch.bfloat16,
    )
tokenizer = AutoTokenizer.from_pretrained(
        model_path
)

conv = [{"role": "user", "content":"Take a current environmental issue and work backward to devise an innovative prevention strategy that could have averted the problem."}]

input_ids = tokenizer.apply_chat_template(conv, return_tensors="pt", thinking=True, return_dict=True, add_generation_prompt=True).to(device)

set_seed(42)
output = model.generate(
    **input_ids,
    max_new_tokens=8192,
)

prediction = tokenizer.decode(output[0, input_ids["input_ids"].shape[1]:], skip_special_tokens=True)
print(prediction)
```

**Example Outputs**
**Example Outputs**
- thinking=True
```md
<think>To address this task, I'll choose the environmental issue of plastic pollution in our oceans as a current problem. I'll then work backward to devise an innovative prevention strategy that could have mitigated this issue.

### Current Environmental Issue: Plastic Pollution in Oceans

**Problem Description:**
- Millions of tons of plastic waste enter oceans annually, causing severe harm to marine life, ecosystems, and human health.
- Plastic debris persists in the environment for centuries, breaking down into microplastics that contaminate food chains.

### Working Backward to Develop a Prevention Strategy

1. **Identify Root Causes:**
   - **Consumer Behavior:** Over-reliance on single-use plastics (e.g., bags, bottles, straws).
   - **Manufacturing Practices:** Production of non-biodegradable plastics.
   - **Waste Management:** Inadequate recycling infrastructure and littering.

2. **Develop a Prevention Strategy:**
   - **Innovative Product Design:**
     - **Biodegradable Alternatives:** Develop and promote the use of biodegradable plastics made from renewable resources like plant starches, seaweed, or algae. These materials would decompose naturally in marine environments without causing harm.
     - **Reusable and Recyclable Packaging:** Encourage manufacturers to design products with minimal packaging or use fully recyclable materials. Implement a robust recycling system that ensures materials are effectively recycled into new products.

   - **Policy and Regulation:**
     - **Bans and Restrictions:** Implement strict bans on single-use plastics in retail and public spaces. Encourage governments to adopt policies that incentivize the use of sustainable materials.
     - **Extended Producer Responsibility (EPR):** Introduce EPR policies where manufacturers are responsible for the entire lifecycle of their products, including disposal and recycling.

   - **Education and Awareness:**
     - **Public Campaigns:** Launch global campaigns to educate consumers about the impact of plastic pollution and promote sustainable alternatives.
     - **School and Community Programs:** Integrate environmental education into school curricula and community programs to foster a culture of sustainability from a young age.

   - **Technological Innovations:**
     - **Advanced Recycling Technologies:** Invest in research and development of advanced recycling technologies that can effectively break down plastic waste into usable materials, reducing the need for virgin plastics.
     - **Ocean Cleanup Technologies:** Support the development and deployment of technologies that can remove plastic waste from oceans, such as autonomous drones or floating barriers.

3. **Implementation and Monitoring:**
   - **Collaboration:** Partner with governments, NGOs, and private sectors to implement these strategies at scale.
   - **Monitoring and Evaluation:** Establish metrics to track the effectiveness of interventions, such as reductions in plastic waste and improvements in marine health.

### Conclusion

By focusing on innovative product design, policy changes, public education, and technological advancements, a comprehensive prevention strategy could have significantly reduced plastic pollution in our oceans. This approach not only addresses the root causes but also fosters a sustainable shift in consumer and industrial behavior, ultimately protecting marine ecosystems and human health.</think><response>### Innovative Prevention Strategy for Plastic Pollution in Oceans

**Current Environmental Issue:** Plastic pollution in oceans, causing severe harm to marine life, ecosystems, and human health.

**Working Backward to Develop a Prevention Strategy:**

1. **Identify Root Causes:**
   - **Consumer Behavior:** Over-reliance on single-use plastics.
   - **Manufacturing Practices:** Production of non-biodegradable plastics.
   - **Waste Management:** Inadequate recycling infrastructure and littering.

2. **Develop a Comprehensive Prevention Strategy:**

   - **Innovative Product Design:**
     - **Biodegradable Alternatives:**
       - Develop and promote the use of biodegradable plastics from renewable resources like plant starches, seaweed, or algae. These materials would decompose naturally in marine environments.
       - Encourage the use of compostable materials for food packaging and other disposable items.
     - **Reusable and Recyclable Packaging:**
       - Design products with minimal packaging or use fully recyclable materials.
       - Implement a robust recycling system that ensures materials are effectively recycled into new products.

   - **Policy and Regulation:**
     - **Bans and Restrictions:**
       - Implement strict bans on single-use plastics in retail and public spaces.
       - Encourage governments to adopt policies that incentivize the use of sustainable materials.
     - **Extended Producer Responsibility (EPR):**
       - Introduce EPR policies where manufacturers are responsible for the entire lifecycle of their products, including disposal and recycling.

   - **Education and Awareness:**
     - **Public Campaigns:**
       - Launch global campaigns to educate consumers about the impact of plastic pollution and promote sustainable alternatives.
     - **School and Community Programs:**
       - Integrate environmental education into school curricula and community programs to foster a culture of sustainability from a young age.

   - **Technological Innovations:**
     - **Advanced Recycling Technologies:**
       - Invest in research and development of advanced recycling technologies that can effectively break down plastic waste into usable materials, reducing the need for virgin plastics.
     - **Ocean Cleanup Technologies:**
       - Support the development and deployment of technologies that can remove plastic waste from oceans, such as autonomous drones or floating barriers.

3. **Implementation and Monitoring:**
   - **Collaboration:** Partner with governments, NGOs, and private sectors to implement these strategies at scale.
   - **Monitoring and Evaluation:** Establish metrics to track the effectiveness of interventions, such as reductions in plastic waste and improvements in marine health.

### Conclusion

By focusing on innovative product design, policy changes, public education, and technological advancements, a comprehensive prevention strategy could have significantly reduced plastic pollution in our oceans. This approach not only addresses the root causes but also fosters a sustainable shift in consumer and industrial behavior, ultimately protecting marine ecosystems and human health.</response>

```
- thinking=False
```md
Current Environmental Issue: Plastic Pollution in Oceans

Prevention Strategy: Implementing a Global Extended Producer Responsibility (EPR) Program for Single-Use Plastics

Background: Plastic pollution in oceans is a pressing environmental issue, with millions of tons of plastic waste entering marine ecosystems annually. This pollution harms marine life, disrupts food chains, and poses risks to human health through microplastics in seafood and contaminated seafood products.

Innovative Prevention Strategy: Implementing a Global Extended Producer Responsibility (EPR) Program for Single-Use Plastics

1. Establish a Global EPR Framework: Create an international agreement among governments, industries, and environmental organizations to establish a comprehensive EPR program for single-use plastics. This framework would hold manufacturers, distributors, and retailers accountable for the entire lifecycle of their plastic products, including collection, recycling, and disposal.

2. Set Clear Targets and Timelines: Define specific, measurable, achievable, relevant, and time-bound (SMART) targets for reducing single-use plastic waste, such as phasing out certain items (e.g., plastic bags, straws, and cutlery) within a set timeframe (e.g., 5 years).

3. Incentivize Sustainable Alternatives: Encourage the development, production, and adoption of eco-friendly alternatives to single-use plastics by offering financial incentives, tax breaks, or subsidies to companies that invest in sustainable packaging and materials.

4. Strengthen Collection and Recycling Infrastructure: Invest in and expand waste management systems, particularly in developing countries, to ensure proper collection, sorting, and recycling of plastic waste. This includes supporting the development of advanced recycling technologies, such as chemical recycling, to handle hard-to-recycle plastics.

5. Promote Consumer Awareness and Education: Launch global campaigns to raise public awareness about the environmental impacts of single-use plastics and encourage responsible consumption. Educate consumers on proper waste disposal, recycling, and the benefits of choosing sustainable alternatives.

6. Enforce Strict Regulations and Penalties: Implement and enforce stringent regulations on the production, distribution, and sale of single-use plastics, with severe penalties for non-compliance. This includes banning or taxing harmful plastic products and promoting transparency in supply chains.

7. Foster International Collaboration: Encourage information sharing, best practices, and joint initiatives among countries to address plastic pollution effectively. This includes supporting research and development of innovative solutions, such as biodegradable plastics and ocean cleanup technologies.

8. Monitor and Evaluate Progress: Regularly assess the effectiveness of the EPR program and make necessary adjustments to ensure continuous improvement. This includes tracking key performance indicators (KPIs) such as plastic waste reduction, recycling rates, and the adoption of sustainable alternatives.

By implementing this innovative prevention strategy, we can significantly reduce the amount of single-use plastic waste entering our oceans, mitigating the environmental and health impacts associated with plastic pollution.
```

**Evaluation Results:**
<table>
<thead>
    <caption style="text-align:center"><b>Comparison with different models over various benchmarks<sup id="fnref1"><a href="#fn1">1</a></sup>. Scores of AlpacaEval-2.0 and Arena-Hard are calculated with thinking=True</b></caption>
  <tr>
    <th style="text-align:left; background-color: #001d6c; color: white;">Models</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">Arena-Hard</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">AlpacaEval-2.0</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">MMLU</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">PopQA</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">TruthfulQA</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">BigBenchHard<sup id="fnref2"><a href="#fn2">2</a></sup></th>
    <th style="text-align:center; background-color: #001d6c; color: white;">DROP<sup id="fnref3"><a href="#fn3">3</a></sup></th>
    <th style="text-align:center; background-color: #001d6c; color: white;">GSM8K</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">HumanEval</th>
   <th style="text-align:center; background-color: #001d6c; color: white;">HumanEval+</th>
  <th style="text-align:center; background-color: #001d6c; color: white;">IFEval</th>
  <th style="text-align:center; background-color: #001d6c; color: white;">AttaQ</th>
  </tr></thead>
  <tbody>
<tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.1-2B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">23.3</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">27.17</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">57.11</td> 
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">20.55</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">59.79</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">61.82</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">20.99</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">67.55</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">79.45</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">75.26</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">63.59</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">84.7</td>
  </tr>
  <tr>
      <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.2-2B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">24.86</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">34.51</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">57.18</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">20.56</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">59.8</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">61.39</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">23.84</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">67.02</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">80.13</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">73.39</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">61.55</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">83.23</td>
  </tr>
  <tr>
      <td style="text-align:left; background-color: #DAE8FF; color: black;"><b>Granite-3.3-2B-Instruct</b></td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 28.86 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 43.45 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 55.88 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 18.4 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 58.97 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 63.91 </td>
      <td style="text-align:center; background-color: #DAE8FF; color: black;"> 44.33 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 72.48 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 80.51 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 75.68 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 65.8 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">87.47</td>
      </tr>
      
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Llama-3.1-8B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">36.43</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">27.22</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">69.15</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">28.79</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">52.79</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">73.43</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">71.23</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">83.24</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">85.32</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">80.15</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">79.10</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">83.43</td>
  </tr>
           
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">DeepSeek-R1-Distill-Llama-8B</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">17.17</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">21.85</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">45.80</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">13.25</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">47.43</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">67.39</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">49.73</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">72.18</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">67.54</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">62.91</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">66.50</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">42.87</td>
  </tr>
      
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Qwen-2.5-7B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">25.44</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">30.34</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">74.30</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">18.12</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">63.06</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">69.19</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">64.06</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">84.46</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">93.35</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">89.91</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">74.90</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">81.90</td>
  </tr>
      
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">DeepSeek-R1-Distill-Qwen-7B</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">10.36</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">15.35</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">50.72</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">9.94</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">47.14</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">67.38</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">51.78</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">78.47</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">79.89</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">78.43</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">59.10</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">42.45</td>
  </tr>
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.1-8B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">37.58</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">30.34</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">66.77</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">28.7</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">65.84</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">69.87</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">58.57</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">79.15</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">89.63</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">85.79</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">73.20</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">85.73</td>
  </tr>
            
<tr>
      <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.2-8B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">55.25</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">61.19</td>
   <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">66.79</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">28.04</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">66.92</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">71.86</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">58.29</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">81.65</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">89.35</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">85.72</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">74.31</td>
     <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;">84.7</td>
  </tr>
  <tr>
      <td style="text-align:left; background-color: #DAE8FF; color: black;"><b>Granite-3.3-8B-Instruct</b></td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 57.56 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 62.68 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 65.54 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 26.17 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 66.86 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 69.13 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 59.36 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 80.89 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 89.73 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 86.09 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 74.82 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">88.5</td>
      </tr>                 
</tbody></table>

<table>
 <caption style="text-align:center"><b>Math Benchmarks</b></caption>
<thead>
  <tr>
    <th style="text-align:left; background-color: #001d6c; color: white;">Models</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">AIME24</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">MATH-500</th>
  </tr></thead>
  <tbody>
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.1-2B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 0.89 </td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 35.07 </td>
  </tr>
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.2-2B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 0.89 </td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 35.54 </td>
  </tr>
  <tr>
      <td style="text-align:left; background-color: #DAE8FF; color: black;"><b>Granite-3.3-2B-Instruct</b></td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 3.28 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 58.09 </td>
  </tr>
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.1-8B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 1.97 </td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 48.73 </td>
  </tr>
  <tr>
    <td style="text-align:left; background-color: #FFFFFF; color: #2D2D2D;">Granite-3.2-8B-Instruct</td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 2.43 </td>
    <td style="text-align:center; background-color: #FFFFFF; color: #2D2D2D;"> 52.8 </td>
  </tr>
  <tr>
      <td style="text-align:left; background-color: #DAE8FF; color: black;"><b>Granite-3.3-8B-Instruct</b></td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 8.12 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 69.02 </td>
  </tr>
    </tbody></table>
<!-- <table>
  <caption><b>Thinking Ablation</b></caption>
<thead>
  <tr>
    <th rowspan="4" style="text-align:left; background-color: #001d6c; color: white;">Models</th>
    <th colspan="4" style="text-align:center; background-color: #001d6c; color: white;">Thinking=False</th>
    <th colspan="4" style="text-align:center; background-color: #001d6c; color: white;">Thinking=True</th>
  </tr>
  <tr>
    <th style="text-align:center; background-color: #001d6c; color: white;">ArenaHard</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">Alpaca-Eval-2</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">AIME24</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">MATH500</th>      
    <th style="text-align:center; background-color: #001d6c; color: white;">ArenaHard</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">Alpaca-Eval-2</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">AIME24</th>
    <th style="text-align:center; background-color: #001d6c; color: white;">MATH500</th>    
  </tr></thead>
    <tbody>
         <tr>
    <td style="text-align:left; background-color: #DAE8FF; color: black;">Granite-3.1-2B-Instruct</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">23.3</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">27.17</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">0.89</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">35.07</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
        </tr>
         <tr>
    <td style="text-align:left; background-color: #DAE8FF; color: black;">Granite-3.2-2B-Instruct</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">30.42</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">31.65</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">0.94</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">37.15</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">26.6</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">34.51</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">0.89</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">35.54</td>
        </tr>
         <tr>
    <td style="text-align:left; background-color: #DAE8FF; color: black;">Granite-3.3-2B-Instruct</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> - </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> - </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 28.86 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 43.45 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">3.28</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">58.09</td>
        </tr>
         <tr>
    <td style="text-align:left; background-color: #DAE8FF; color: black;">Granite-3.1-8B-Instruct</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">37.58</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">30.34</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">1.97</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">48.73</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
        </tr>
         <tr>
    <td style="text-align:left; background-color: #DAE8FF; color: black;">Granite-3.2-8B-Instruct</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">40.54</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">36.89</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">3.13</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">50.78</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">55.25</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">61.19</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">2.43</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">52.8</td>
        </tr>
         <tr>
    <td style="text-align:left; background-color: #DAE8FF; color: black;">Granite-3.3-8B-Instruct</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> - </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> - </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">-</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 57.56 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;"> 62.68 </td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">8.12</td>
    <td style="text-align:center; background-color: #DAE8FF; color: black;">69.02</td>
        </tr>
    </table>
    <tbody> -->

**Training Data:** 
Overall, our training data is largely comprised of two key sources: (1) publicly available datasets with permissive license, (2) internal synthetically generated data targeted to enhance reasoning capabilites. 
<!-- A detailed attribution of datasets can be found in [Granite 3.2 Technical Report (coming soon)](#), and [Accompanying Author List](https://github.com/ibm-granite/granite-3.0-language-models/blob/main/author-ack.pdf). -->

**Infrastructure:**
We train Granite-3.3-2B-Instruct using IBM's super computing cluster, Blue Vela, which is outfitted with NVIDIA H100 GPUs. This cluster provides a scalable and efficient infrastructure for training our models over thousands of GPUs.

**Ethical Considerations and Limitations:** 
Granite-3.3-2B-Instruct builds upon Granite-3.3-2B-Base, leveraging both permissively licensed open-source and select proprietary data for enhanced performance. Since it inherits its foundation from the previous model, all ethical considerations and limitations applicable to [Granite-3.3-2B-Base](https://huggingface.co/ibm-granite/granite-3.3-2b-base) remain relevant.


**Resources**
- ⭐️ Learn about the latest updates with Granite: https://www.ibm.com/granite
- 📄 Get started with tutorials, best practices, and prompt engineering advice: https://www.ibm.com/granite/docs/
- 💡 Learn about the latest Granite learning resources: https://github.com/ibm-granite-community/



<p><a href="#fnref1" title="Jump back to reference">[1]</a> Evaluated using <a href="https://github.com/allenai/olmes">OLMES</a> (except AttaQ and Arena-Hard scores)</p>
<p><a href="#fnref2" title="Jump back to reference">[2]</a> Added regex for more efficient asnwer extraction.</a></p>
<p><a href="#fnref2" title="Jump back to reference">[2]</a> Modified the implementation to handle some of the issues mentioned <a href="https://huggingface.co/blog/open-llm-leaderboard-drop">here</a></p>

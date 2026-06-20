<script>
    $( document ).ready(function() {
        mapDataToFormUI({'frm_wizard':"/api/core/initial_setup/get"}).done(function(data){
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
        });
        /* next pane */
        $(".action_next").click(function(){
            let target = $("#tab_" + $(this).data('next_index'));
            let advance = function () {
                target.parent().removeClass('hidden');
                target.click();
            };
            let this_form = $(this).closest('.tab-pane').find('form');
            if (this_form.length === 0) {
                /* informational step without inputs, nothing to validate */
                advance();
                return;
            }
            /* Validate and store only the fields of the current step. The model
               is in-memory (the final apply step re-submits every field at once),
               so validating the whole form here would fail on required fields
               that belong to later steps and the Next button would silently do
               nothing. Keeping the dialog enabled shows the user what to fix. */
            saveFormToEndpoint("/api/core/initial_setup/set", this_form.attr('id'), advance, false);
        });
        $("#wizard\\.interfaces\\.wan\\.ipv4_type").change(function(){
            $(".wan_options").closest('tr').hide();
            $(".wan_options_" + $(this).val()).closest('tr').show();
        });

        $("#apply").click(function(){
            let this_button = $(this);
            if (this_button.hasClass('running')) {
                return;
            } else {
                this_button.find('.reload_progress').addClass("fa fa-spinner fa-pulse");
                this_button.addClass('running');
                saveFormToEndpoint("/api/core/initial_setup/configure", 'form_root', function(data){
                        this_button.removeClass('running');
                        this_button.find('.reload_progress').removeClass("fa fa-spinner fa-pulse");
                        /* redirect to index for finish page */
                        window.location = '/index.php?wizard_done';
                    },
                    false,
                    function (data) {
                        this_button.removeClass('running');
                        this_button.find('.reload_progress').removeClass("fa fa-spinner fa-pulse");
                    }
                );
            }
        });
        $("#abort").click(function(){
            ajaxCall("/api/core/initial_setup/abort",{}, function(){
                /* redirect to index for finish page */
                window.location = '/index.php?wizard_done';
            });
        });
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
{% for tabid, tab in all_tabs%}
    <li class="{% if loop.first %}active{% else %}hidden{% endif %}"><a data-toggle="tab" id="tab_{{loop.index}}" href="#{{tabid}}">{{ tab['title'] }}</a></li>
{% endfor %}
</ul>
<div class="tab-content content-box" id="form_root">
    {% for tabid, tab in all_tabs%}
    <div id="{{tabid}}" class="tab-pane fade in {% if loop.first %}active{% endif %}" style="padding-top:10px;">
        <div class="col-md-12">
            {% if tab['message'] is defined %}
                <div class="well">
                    {{ tab['message'] }}
                </div>
            {% elseif tab['form'] is defined %}
                {{ partial("layout_partials/base_form",['fields': tab['form'], 'id': 'frm_wizard-' ~ tabid ])}}
                <br/>
            {% endif %}

            {% if not loop.last %}
            <button class="btn btn-primary action_next" id="btn_next_{{loop.index}}" data-next_index="{{loop.index + 1}}">
                <b>{{ lang._('Next') }}</b>
            </button>
                {% if loop.first %}
                <button class="btn btn-primary pull-right" id="abort">
                    <b>{{ lang._('Abort') }}</b>
                </button>
                {% endif %}
            {% else %}
            <button class="btn btn-primary" id="apply">
                <b>{{ lang._('Apply') }}</b><i class="reload_progress"></i>
            </button>
            {% endif %}
            <br/><br/>
        </div>
    </div>
    {% endfor %}
</div>
